"""Optional Gemini semantic review adapter for AG-AUD.

The SDK is imported lazily so the deterministic auditor keeps working when
`google-generativeai` is not installed and so tests that run with AI disabled
never touch the network or the SDK.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .checks import CheckOutcome
from .config import Settings
from .models import AuditRequest

CONFIGURE_LOCK = threading.Lock()
MAX_PROMPT_CHARS = 12_000
MAX_EVIDENCE_CHARS = 600

SEMANTIC_CRITERIA = (
    "Ortografía, Gramática y Claridad",
    "Coherencia Texto–Imagen–Idea",
    "Alineación Estratégica y Audiencia",
    "Voz, Tono y Reglas de Marca",
    "Riesgo y Afirmaciones No Sustentadas",
)

SYSTEM_PROMPT = """Eres AG-AUD, el auditor de calidad del Retail Content Factory.
Evalúa únicamente los cinco criterios semánticos indicados.
No inventes hechos, precios, descuentos, vigencias ni fuentes.
Devuelve SOLO un objeto JSON válido, sin markdown, con esta forma:
{"checks":[{"criterio":"<criterio exacto>","resultado":"PASSED|FAILED",
"evidencia":"<máximo 600 caracteres>","hallazgo":"<texto o null>"}]}
"""


class GeminiResponseError(RuntimeError):
    """Raised when the model output cannot be trusted."""


@dataclass(frozen=True)
class AiCheck:
    criterio: str
    passed: bool
    evidencia: str
    hallazgo: str | None = None


class AuditAI(Protocol):
    def review(self, request: AuditRequest) -> dict[str, AiCheck]: ...


class _AiCheckPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    criterio: str
    resultado: Literal["PASSED", "FAILED"]
    evidencia: str = Field(min_length=1, max_length=MAX_EVIDENCE_CHARS)
    hallazgo: str | None = Field(default=None, max_length=MAX_EVIDENCE_CHARS)


class _AiReviewPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    checks: list[_AiCheckPayload] = Field(min_length=1, max_length=20)


def _canonical_criterion(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.casefold()).strip()
    return normalized.replace("\u2013", "-").replace("\u2014", "-")


_CRITERION_LOOKUP = {
    _canonical_criterion(criterion): criterion for criterion in SEMANTIC_CRITERIA
}


def _match_criterion(value: str) -> str | None:
    return _CRITERION_LOOKUP.get(_canonical_criterion(value))


def parse_review(raw: str) -> dict[str, AiCheck]:
    """Parse and strictly validate a Gemini JSON review.

    Tolerates markdown fences and surrounding prose, but every recognised
    criterion must be one of the five semantic names. Unknown criteria are
    ignored; duplicates and empty reviews are rejected.
    """

    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise GeminiResponseError("respuesta sin objeto JSON")

    try:
        payload = _AiReviewPayload.model_validate_json(text[start : end + 1])
    except ValueError as exc:
        raise GeminiResponseError("respuesta JSON inválida") from exc

    result: dict[str, AiCheck] = {}
    for item in payload.checks:
        canonical = _match_criterion(item.criterio)
        if canonical is None:
            continue
        if canonical in result:
            raise GeminiResponseError("criterio duplicado")
        evidence = item.evidencia.strip()
        if not evidence:
            raise GeminiResponseError("evidencia vacía")
        result[canonical] = AiCheck(
            criterio=canonical,
            passed=item.resultado == "PASSED",
            evidencia=evidence,
            hallazgo=(item.hallazgo or "").strip() or None,
        )

    if not result:
        raise GeminiResponseError("sin criterios reconocidos")
    return result


def build_prompt(request: AuditRequest) -> str:
    """Serialize the request into a bounded, deterministic review prompt."""

    payload = json.dumps(
        request.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
    )
    if len(payload) > MAX_PROMPT_CHARS:
        raise GeminiResponseError("payload demasiado grande para la revisión")
    criterios = "\n".join(f"- {name}" for name in SEMANTIC_CRITERIA)
    return f"Criterios a evaluar:\n{criterios}\n\nInvocación:\n{payload}"


class GeminiAuditor:
    """Thin adapter around the legacy `google.generativeai` SDK."""

    def __init__(self, api_key: str, model_name: str, timeout: float) -> None:
        import google.generativeai as genai  # lazy import

        with CONFIGURE_LOCK:
            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=SYSTEM_PROMPT,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    top_p=1.0,
                    candidate_count=1,
                    max_output_tokens=2048,
                    response_mime_type="application/json",
                ),
            )
        self._timeout = timeout

    def review(self, request: AuditRequest) -> dict[str, AiCheck]:
        response = self._model.generate_content(
            build_prompt(request),
            request_options={"timeout": self._timeout},
        )
        return parse_review(response.text)


def build_gemini_auditor(settings: Settings) -> GeminiAuditor | None:
    """Create the adapter only when AI is enabled and a credential exists."""

    if not settings.ai_enabled or settings.ai_key is None:
        return None
    return GeminiAuditor(
        api_key=settings.ai_key.get_secret_value(),
        model_name=settings.ai_model,
        timeout=settings.ai_timeout_seconds,
    )


def reconcile_outcomes(
    deterministic: list[CheckOutcome],
    review: dict[str, AiCheck],
) -> list[CheckOutcome]:
    """Fail-closed merge of deterministic outcomes with the AI review.

    Gemini may add a failure to a deterministic pass, but it can never turn a
    deterministic failure into a pass. Weight, severity, route and sources are
    always copied from the deterministic engine.
    """

    reconciled: list[CheckOutcome] = []
    for outcome in deterministic:
        ai = review.get(outcome.criterio)
        if ai is None or outcome.criterio not in SEMANTIC_CRITERIA:
            reconciled.append(outcome)
            continue

        if not outcome.passed:
            evidence = outcome.evidencia
            if ai.passed:
                evidence = f"{evidence} | Gemini no detectó problemas adicionales."
            elif ai.evidencia not in evidence:
                evidence = f"{evidence} | Gemini: {ai.evidencia}"
            reconciled.append(
                replace(
                    outcome,
                    passed=False,
                    evidencia=evidence,
                    hallazgo=outcome.hallazgo or ai.hallazgo or ai.evidencia,
                )
            )
            continue

        if ai.passed:
            reconciled.append(replace(outcome, passed=True, evidencia=ai.evidencia))
            continue

        reconciled.append(
            replace(
                outcome,
                passed=False,
                evidencia=ai.evidencia,
                hallazgo=ai.hallazgo or ai.evidencia,
            )
        )
    return reconciled
