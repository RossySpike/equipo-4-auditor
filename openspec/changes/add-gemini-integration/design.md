# Design

## Context

See `proposal.md` for motivation. The current service is deliberately deterministic:

- `app/config.py` defines a frozen dataclass and reads `AUDITOR_*` variables with `os.getenv` on every request.
- `app/service.py::run_audit` calls `run_checks`, applies fixed weights, computes the verdict, routes rejections, and signs approvals.
- `app/main.py` returns an `AuditResponse` Pydantic model; FastAPI serialises it to the JSON v1.0 envelope.
- `tests/` asserts exact scores, verdicts, check names and order, evidence substrings, `data_sources`, hash/signature determinism, warning order, and trace fields.

That suite is the contract to preserve. The repository `.env` contains `AI_KEY`; `AI_KEY` must never be copied into code, tests, traces, logs, or artifacts. Neither `pydantic-settings` nor `google-generativeai` is installed yet.

## Goals / Non-Goals

**Goals:**

- Read `AI_KEY` from the repository `.env` with `pydantic-settings`, keeping process environment variables authoritative.
- Instantiate `google.generativeai` lazily and only when AI review is explicitly enabled.
- Give Gemini a bounded, structured review of the five semantic checks.
- Preserve the response dict exactly, including score, verdict, routing, sources, approval fields, warnings, errors, and timestamps.
- Guarantee that the existing pytest suite passes unchanged with AI disabled.
- Degrade to the deterministic audit on every AI failure without leaking the credential.

**Non-Goals:**

- Replacing deterministic veracity or Instagram-limits checks with model output.
- Image/multimodal analysis, streaming, chat history, embeddings, or function calling.
- Changing routes, request models, response models, HTTP status codes, hash format, or signature algorithm.
- Model-driven scoring, severity, routing, data sources, approval IDs, or signatures.
- Secret managers, key rotation, or per-tenant credentials; deployment still supplies environment variables.
- Migrating to the newer `google-genai` SDK in this change.

## Decisions

### 1. `Settings` becomes a `BaseSettings` loaded per request

Use `pydantic-settings` so `.env` parsing, type coercion, aliases, and secret handling are declarative. Keep `load_settings()` as the only factory so call sites do not change.

```python
# app/config.py
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEVELOPMENT_SIGNING_KEY = "dev-insecure-auditor-signing-key"
DEFAULT_APPROVAL_THRESHOLD = 85
DEFAULT_TRACE_PATH = Path("data/audit_trace.jsonl")
DEFAULT_AI_MODEL = "gemini-2.5-flash"
DEFAULT_AI_TIMEOUT_SECONDS = 20.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="AUDITOR_",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    signing_key: str | None = None                 # AUDITOR_SIGNING_KEY
    approval_threshold: int = DEFAULT_APPROVAL_THRESHOLD
    trace_path: Path = DEFAULT_TRACE_PATH
    max_caption_chars: int = 2200
    max_hashtags: int = 30
    allowed_image_formats: tuple[str, ...] = ("jpg", "jpeg", "png", "webp")

    # validation_alias bypasses env_prefix only for this field.
    ai_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AI_KEY", "AUDITOR_AI_KEY"),
    )
    ai_enabled: bool = False                       # AUDITOR_AI_ENABLED
    ai_model: str = DEFAULT_AI_MODEL               # AUDITOR_AI_MODEL
    ai_timeout_seconds: float = DEFAULT_AI_TIMEOUT_SECONDS

    @field_validator("ai_key", mode="before")
    @classmethod
    def _blank_ai_key_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("approval_threshold", mode="before")
    @classmethod
    def _threshold_or_default(cls, value: object) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return DEFAULT_APPROVAL_THRESHOLD
        return parsed if 0 <= parsed <= 100 else DEFAULT_APPROVAL_THRESHOLD

    @property
    def using_development_key(self) -> bool:
        return not self.signing_key

    @property
    def resolved_signing_key(self) -> str:
        return self.signing_key or DEVELOPMENT_SIGNING_KEY


def load_settings() -> Settings:
    return Settings()
```

Key points:

- `PROJECT_ROOT / ".env"` is absolute, so loading does not depend on the process working directory.
- `Settings()` is constructed per request, as before. This keeps `monkeypatch.setenv` effective and lets deployments change the environment without a restart.
- `pydantic-settings` gives environment variables precedence over `.env` values by default.
- `AI_KEY="..."` is unquoted by `python-dotenv` during `.env` parsing; the value is wrapped in `SecretStr`, so `repr(settings)` and validation errors do not print it.
- `AUDITOR_AI_*` fields use the configured `AUDITOR_` prefix. Only `ai_key` needs an explicit alias because its variable is `AI_KEY`, not `AUDITOR_AI_KEY`.
- `settings.signing_key` keeps the existing meaning; `service.py` switches from `settings.signing_key` to `settings.resolved_signing_key`.
- `max_caption_chars`, `max_hashtags`, and `allowed_image_formats` are preserved with their current defaults so any code constructing `Settings` keeps working, even though the current service does not read them.

### 2. The Gemini adapter lives in `app/gemini.py`

`config.py` must not import `google.generativeai`. A lazy import keeps `app.main`, the models, and the deterministic path importable even if the SDK is missing, and it makes AI-off tests independent of the dependency.

```python
# app/gemini.py (shape, not the full file)
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .models import AuditRequest

_CONFIGURE_LOCK = threading.Lock()

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


MAX_PROMPT_CHARS = 12_000


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
```

The concrete client uses the legacy SDK exactly as requested:

```python
class GeminiAuditor:
    def __init__(self, api_key: str, model_name: str, timeout: float) -> None:
        import google.generativeai as genai  # lazy import

        with _CONFIGURE_LOCK:
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
    if not settings.ai_enabled or settings.ai_key is None:
        return None
    return GeminiAuditor(
        api_key=settings.ai_key.get_secret_value(),
        model_name=settings.ai_model,
        timeout=settings.ai_timeout_seconds,
    )
```

- `google.generativeai` is the SDK named in the request. It is legacy; the adapter isolates it so a future migration to `google-genai` does not touch `service.py`.
- `genai.configure` mutates process-global SDK state. A module-level `threading.Lock` serialises configuration/model creation. The key is passed to `configure`, not stored on `GeminiAuditor`.
- Temperature `0`, one candidate, `response_mime_type="application/json"`, and `max_output_tokens=2048` make reviews as reproducible and bounded as the API allows.
- `request_options={"timeout": ...}` bounds each call. No automatic retry is added in this change; the orchestrator already owns retry policy and the service has a safe fallback.

### 3. Prompt construction and response parsing

`build_prompt(request)` serialises the validated request without `None` values:

```python
def build_prompt(request: AuditRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
    )
    if len(payload) > MAX_PROMPT_CHARS:  # 12_000
        raise GeminiResponseError("payload demasiado grande para la revisión")
    criterios = "\n".join(f"- {name}" for name in SEMANTIC_CRITERIA)
    return f"Criterios a evaluar:\n{criterios}\n\nInvocación:\n{payload}"
```

The parser is strict and never trusts model metadata:

```python
class _AiCheckPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    criterio: str
    resultado: Literal["PASSED", "FAILED"]
    evidencia: str = Field(min_length=1, max_length=600)
    hallazgo: str | None = Field(default=None, max_length=600)


class _AiReviewPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    checks: list[_AiCheckPayload] = Field(min_length=1, max_length=20)


def parse_review(raw: str) -> dict[str, AiCheck]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise GeminiResponseError("respuesta sin objeto JSON")
    payload = _AiReviewPayload.model_validate_json(text[start : end + 1])

    result: dict[str, AiCheck] = {}
    for item in payload.checks:
        canonical = _match_criterion(item.criterio)
        if canonical is None:
            continue
        if canonical in result:
            raise GeminiResponseError("criterio duplicado")
        result[canonical] = AiCheck(
            criterio=canonical,
            passed=item.resultado == "PASSED",
            evidencia=item.evidencia.strip(),
            hallazgo=item.hallazgo.strip() if item.hallazgo else None,
        )
    if not result:
        raise GeminiResponseError("sin criterios reconocidos")
    return result
```

`_match_criterion` compares case-folded, whitespace-normalised text after replacing `–`/`—` with `-`; only the five canonical names are accepted. Unknown criteria are ignored, duplicates fail the whole review, and an empty review fails. Any `GeminiResponseError`, `ValueError`, SDK exception, timeout, quota error, or safety block propagates to `service.py`, which applies the fallback.

### 4. Fail-closed reconciliation

`reconcile_outcomes` returns a new list with the same length and order as `run_checks`:

```python
from dataclasses import replace

def reconcile_outcomes(
    deterministic: list[CheckOutcome],
    review: dict[str, AiCheck],
) -> list[CheckOutcome]:
    reconciled: list[CheckOutcome] = []
    for outcome in deterministic:
        ai = review.get(outcome.criterio)
        if ai is None or outcome.criterio not in SEMANTIC_CRITERIA:
            reconciled.append(outcome)
            continue

        if not outcome.passed:
            # Deterministic failure always wins; keep its evidence and add AI context.
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

        # AI may add a failure, but never remove one.
        reconciled.append(
            replace(
                outcome,
                passed=False,
                evidencia=ai.evidencia,
                hallazgo=ai.hallazgo or ai.evidencia,
            )
        )
    return reconciled
```

`weight`, `severe`, `route`, and `data_sources` are copied untouched from the deterministic `CheckOutcome`. Therefore:

- `score = max(0, 100 - Σ weight of failed outcomes)` is unchanged in formula.
- `severe_failure` still comes from the fixed check metadata.
- `return_to` still follows `AG-EST` → `AG-BOR` → `AG-COP`.
- `data_sources` still comes only from the deterministic veracity check.
- AI can tighten the audit (pass → fail) but never loosen it (fail → pass).

### 5. Service wiring and response construction

`run_audit` keeps its public signature and adds an optional injection point for tests. `service.py` adds `logger = logging.getLogger(__name__)` and uses `settings.resolved_signing_key` in the signing call:

```python
def run_audit(
    request: AuditRequest,
    settings: Settings,
    ai_auditor: AuditAI | None = None,
) -> AuditResponse:
    started_at = datetime.now(timezone.utc)
    outcomes = run_checks(request)

    warnings: list[str] = []
    if settings.using_development_key:
        warnings.append(
            "AUDITOR_SIGNING_KEY no está configurada; se usa una clave de desarrollo."
        )

    if settings.ai_enabled:
        auditor = ai_auditor or build_gemini_auditor(settings)
        if auditor is None:
            warnings.append(
                "AUDITOR_AI_ENABLED está activo pero AI_KEY no está configurada; "
                "se usó la auditoría determinista."
            )
        else:
            try:
                outcomes = reconcile_outcomes(outcomes, auditor.review(request))
            except Exception as exc:  # noqa: BLE001 - fallback is the contract
                logger.warning("Gemini audit unavailable: %s", type(exc).__name__)
                warnings.append(
                    "La revisión con Gemini no está disponible; "
                    "se usó la auditoría determinista."
                )

    # From here the code is unchanged: penalty, score, verdict, checks,
    # hallazgos, data_sources, approval_id, hash, signature, response envelope.
```

The response is still built with the existing Pydantic models, never with an ad-hoc dict:

```python
output = AuditOutput(...)
return AuditResponse(
    execution_id=request.execution_id,
    status=status,
    output=output,
    data_sources=_dedupe_sources(outcomes),
    warnings=warnings,
    error=None,
    started_at=started_at,
    finished_at=datetime.now(timezone.utc),
)
```

FastAPI serialises it through `response_model=AuditResponse`; `response.model_dump(mode="json")` is the dict used by tests and by the trace. The serialized shape is exactly:

```json
{
  "contract_version": "1.0",
  "execution_id": "<echoed>",
  "agent_id": "AG-AUD",
  "status": "completed | rejected",
  "output": {
    "veredicto": "APROBADO | RECHAZADO",
    "score": 0,
    "checks": [
      {"criterio": "<canonical>", "resultado": "PASSED | FAILED", "evidencia": "<text>"}
    ],
    "hallazgos": [
      {"criterio": "<canonical>", "detalle": "<text>", "severidad": "alta | media | baja"}
    ],
    "return_to": "AG-EST | AG-BOR | AG-COP | null",
    "approval_id": "APP-YYYYMMDD-xxxxxxxx | null",
    "content_hash": "<64 hex> | null",
    "content_signature": "<64 hex> | null"
  },
  "data_sources": [{"source": "<name>", "ref": "<id>"}],
  "warnings": ["<text>"],
  "error": null,
  "started_at": "<ISO-8601>",
  "finished_at": "<ISO-8601>"
}
```

No field is added, removed, or renamed. The field-by-field mapping is:

| Response field | Source | AI influence |
| --- | --- | --- |
| `contract_version` | model default `"1.0"` | none |
| `execution_id` | request | none |
| `agent_id` | model default `"AG-AUD"` | none |
| `status` | `approved` flag | indirect, via failed checks |
| `output.veredicto` | score + severe failures | indirect |
| `output.score` | fixed weights | indirect |
| `output.checks[]` | reconciled outcomes, canonical order | AI evidence/verdict on five criteria |
| `output.hallazgos[]` | failed outcomes | AI finding when AI adds a failure |
| `output.return_to` | routing priority | indirect |
| `output.approval_id` | UTC timestamp | none |
| `output.content_hash` | asset URL + caption + hashtags | none |
| `output.content_signature` | HMAC with signing key | none |
| `data_sources[]` | deterministic veracity matches | none |
| `warnings[]` | dev key + AI fallback notices | AI adds only on failure |
| `error` | always `None` for audited content | none |
| `started_at` / `finished_at` | UTC clock | none |

### 6. Pytest contract strategy

The existing suite must pass without modification. Two independent mechanisms guarantee it:

1. **AI is opt-in.** `ai_enabled` defaults to `False`, so the presence of `AI_KEY` in `.env` is not enough to call Gemini.
2. **Tests isolate the flag.** Add an autouse fixture to `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _disable_external_ai(monkeypatch):
    monkeypatch.setenv("AUDITOR_AI_ENABLED", "false")
    monkeypatch.delenv("AI_KEY", raising=False)
```

Consequences for the current tests:

| Existing test | Why it still passes with the repository `.env` present |
| --- | --- |
| `test_contract.py::test_health` | `/health` and its response are untouched. |
| `test_contract.py::test_valid_invocation_returns_full_envelope` | AI is off, so `run_checks` produces the same seven `PASSED` outcomes; `warnings` is `[]` because the fixture sets `AUDITOR_SIGNING_KEY`. |
| `test_contract.py::test_reports_the_seven_checks_in_order` | The canonical order comes from `run_checks`, not from Gemini. |
| `test_contract.py::test_reports_commercial_sources` | `data_sources` still comes only from the deterministic veracity check. |
| `test_contract.py` 422 cases | Request models and validation are untouched. |
| `test_checks.py` | Calls `run_checks` directly; the adapter is not involved. |
| `test_verdict.py` | Same fixed weights, score formula, severe flags, threshold, and routing priority. |
| `test_signing_trace.py` | Same hash, signature, approval ID, trace keys, and dev-key warning order. |
| `test_signing_trace.py::test_warns_when_signing_key_is_not_configured` | Still the first warning; AI is off, so no second warning is appended. |

The response is byte-for-byte equivalent to the current one when AI is off, because the only changed inputs to the existing pipeline are the source of `settings` and the optional `ai_auditor` argument.

New tests use dependency injection instead of the network:

```python
class FakeAuditor:
    def __init__(self, review): self.review_result = review
    def review(self, request): return self.review_result

run_audit(request, settings_with_ai, ai_auditor=FakeAuditor({...}))
```

Coverage to add in `tests/test_gemini.py`:

- `Settings(_env_file=tmp_path / ".env")` reads a quoted `AI_KEY`; process env overrides it; blank value becomes `None`; `SecretStr` does not reveal the value in `repr`.
- AI off: `FakeAuditor` is never called and the response equals the deterministic baseline.
- AI on: a semantic pass becomes a failure, score drops by the fixed weight, `hallazgos` gains an entry, and the seven criteria remain in order.
- AI on: a deterministic failure stays failed even if the fake review says `PASSED`.
- Malformed JSON, timeout, and missing criteria raise and produce the deterministic response plus the fallback warning.
- The response dict has exactly the documented envelope and output keys; the key string never appears in `model_dump()` or the trace line.

### 7. Dependencies, `.env`, and documentation

`requirements.txt` additions:

```text
pydantic-settings>=2.2,<3.0
google-generativeai>=0.8,<1.0
```

Add a committed `.env.example` (never the real `.env`):

```dotenv
AI_KEY=
AUDITOR_AI_ENABLED=false
AUDITOR_AI_MODEL=gemini-2.5-flash
AUDITOR_AI_TIMEOUT_SECONDS=20
AUDITOR_SIGNING_KEY=
AUDITOR_APPROVAL_THRESHOLD=85
AUDITOR_TRACE_PATH=data/audit_trace.jsonl
```

Document in `README.md` that `AI_KEY` is secret, that AI is opt-in, the default model, the timeout, and the fallback behavior. Do not print or commit the value from the existing `.env`.

## Implementation Sequence

1. Add `pydantic-settings` and `google-generativeai` to `requirements.txt` and install them.
2. Replace the `dataclass` in `app/config.py` with `Settings(BaseSettings)`, keeping the public names `load_settings`, `using_development_key`, `approval_threshold`, and `trace_path`, and adding `resolved_signing_key`.
3. Create `app/gemini.py` with only the prompt, parser, protocol, and helper code; do not import the SDK at module level.
4. Implement `GeminiAuditor` and `build_gemini_auditor` behind the protocol; verify the constructor arguments with a fake SDK module.
5. Implement `reconcile_outcomes` and unit-test pass→fail, fail→pass, missing criteria, and non-semantic criteria.
6. Change `run_audit` to accept `ai_auditor=None`, build the auditor only when `settings.ai_enabled`, catch AI exceptions, append the fallback warning, and leave the score/verdict/signing block untouched.
7. Switch signing to `settings.resolved_signing_key` and confirm the deterministic suite is unchanged.
8. Add the autouse AI-isolation fixture and the new `tests/test_gemini.py` cases, then run the full suite.
9. Update `README.md` and `.env.example`, run `openspec validate add-gemini-integration --strict`, and record the final test count.

## Risks / Trade-offs

- [Legacy SDK deprecation] → `google-generativeai` is isolated in one adapter; pin `<1.0`; a future `google-genai` migration only replaces `GeminiAuditor`.
- [Model non-determinism] → temperature `0`, JSON mode, strict parser, fixed weights/severity/routing, and fail-closed reconciliation. Deterministic checks remain the floor.
- [AI adds a subjective false failure] → AI is opt-in per deployment, can be disabled with one environment variable, and the deterministic evidence is preserved for diagnosis.
- [Latency and cost] → only five criteria are reviewed, `max_output_tokens=2048`, a 20-second default timeout, no retries, and no calls when disabled.
- [Credential leakage] → `SecretStr`, no key in exceptions/warnings/traces, sanitized log message, `.env` already in `.gitignore`, `.env.example` committed empty.
- [Global `genai.configure` state] → configuration is serialised with a lock and the key is not stored on the client; a deployment uses one key per process.
- [Missing or invalid `AI_KEY`] → `ai_key=None` disables the call and the response carries an explicit warning; the deterministic audit still returns HTTP 200.
- [Malformed or blocked model output] → parser or SDK raises, `run_audit` catches broad exceptions, discards the review, and falls back.
- [`.env` leaking into tests] → autouse fixture forces `AUDITOR_AI_ENABLED=false`; the default is also false.
- [Prompt size or prompt injection from content] → prompt caps the serialized payload at 12,000 characters; the model is instructed to evaluate only the five criteria and never to invent facts; output is never used for sources or scores directly.

## Migration Plan

1. Add the two dependencies and install them in the virtual environment.
2. Deploy the new `config.py`, `gemini.py`, and `service.py` with `AUDITOR_AI_ENABLED` unset/false. Run `python3 -m pytest`; all existing tests must pass unchanged.
3. Put `AI_KEY` in the deployment `.env` (or inject it as an environment variable) and set `AUDITOR_AI_ENABLED=true`.
4. Run canary invocations and compare `score`, `veredicto`, `return_to`, `data_sources`, `content_hash`, and `content_signature` against deterministic output. Inspect warnings for fallbacks.
5. Rollback is configuration-only: set `AUDITOR_AI_ENABLED=false`. Reverting the code and dependencies is the fallback if the adapter causes import or startup issues.
