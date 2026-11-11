"""Tests for the optional Gemini adapter, parser, reconciliation, and wiring."""

from __future__ import annotations

import json
import subprocess
import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from app import gemini as gemini_module
from app.checks import CheckOutcome, run_checks
from app.config import DEFAULT_AI_MODEL, DEFAULT_AI_TIMEOUT_SECONDS, PROJECT_ROOT, Settings
from app.gemini import (
    MAX_EVIDENCE_CHARS,
    MAX_PROMPT_CHARS,
    SEMANTIC_CRITERIA,
    SYSTEM_PROMPT,
    AiCheck,
    GeminiAuditor,
    GeminiResponseError,
    _match_criterion,
    build_gemini_auditor,
    build_prompt,
    parse_review,
    reconcile_outcomes,
)
from app.models import AuditRequest, DataSource
from app.service import run_audit

CRITERIA = list(SEMANTIC_CRITERIA)
SPELLING, COHERENCE, STRATEGY, BRAND, RISK = CRITERIA


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def make_request(payload) -> AuditRequest:
    return AuditRequest.model_validate(payload)


def make_settings(**overrides) -> Settings:
    values = {
        "signing_key": "test-signing-key",
        "approval_threshold": 85,
        "ai_enabled": True,
        "AI_KEY": "test-ai-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def review_json(entries) -> str:
    return json.dumps({"checks": entries}, ensure_ascii=False)


def review_entry(criterion=SPELLING, result="PASSED", evidence="ok", finding=None):
    return {
        "criterio": criterion,
        "resultado": result,
        "evidencia": evidence,
        "hallazgo": finding,
    }


class FakeAuditor:
    def __init__(self, review=None, error: Exception | None = None):
        self.review_result = review or {}
        self.error = error
        self.calls: list[AuditRequest] = []

    def review(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.review_result


def make_outcome(
    criterion,
    *,
    passed=True,
    weight=10,
    severe=False,
    route="AG-COP",
    evidence="deterministic evidence",
    finding=None,
    sources=None,
):
    return CheckOutcome(
        criterio=criterion,
        weight=weight,
        severe=severe,
        route=route,
        passed=passed,
        evidencia=evidence,
        hallazgo=finding,
        data_sources=sources or [],
    )


def all_semantic_pass_review(evidence="AI evidence"):
    return {
        criterion: AiCheck(criterion, True, evidence)
        for criterion in SEMANTIC_CRITERIA
    }


# ---------------------------------------------------------------------------
# Module shape and lazy import
# ---------------------------------------------------------------------------


def test_semantic_criteria_are_the_five_documented_names():
    assert SEMANTIC_CRITERIA == (
        "Ortografía, Gramática y Claridad",
        "Coherencia Texto–Imagen–Idea",
        "Alineación Estratégica y Audiencia",
        "Voz, Tono y Reglas de Marca",
        "Riesgo y Afirmaciones No Sustentadas",
    )
    assert "Veracidad de Datos Comercial" not in SEMANTIC_CRITERIA
    assert "Límites Técnicos de Instagram" not in SEMANTIC_CRITERIA


def test_system_prompt_is_json_only_and_forbids_inventing_facts():
    assert "AG-AUD" in SYSTEM_PROMPT
    assert "No inventes" in SYSTEM_PROMPT
    assert "SOLO un objeto JSON" in SYSTEM_PROMPT
    assert "PASSED|FAILED" in SYSTEM_PROMPT


def test_adapter_module_does_not_import_the_sdk_eagerly():
    code = (
        "import sys; import app.gemini; "
        "print('google.generativeai' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False"


def test_protocol_is_structural_and_ai_check_is_frozen():
    check = AiCheck(criterio=SPELLING, passed=True, evidencia="ok")

    assert FakeAuditor().review is not None
    with pytest.raises(Exception):
        check.passed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Criterion matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("criterion", SEMANTIC_CRITERIA)
def test_match_criterion_accepts_every_canonical_name(criterion):
    assert _match_criterion(criterion) == criterion


def test_match_criterion_normalizes_case_whitespace_and_dashes():
    assert _match_criterion("  ortografía,   gramática y claridad ") == SPELLING
    assert _match_criterion("COHERENCIA TEXTO-IMAGEN-IDEA") == COHERENCE
    assert _match_criterion("Coherencia Texto—Imagen—Idea") == COHERENCE


@pytest.mark.parametrize(
    "unknown",
    ["", "Veracidad de Datos Comercial", "Límites Técnicos de Instagram",
     "Ortografía", "cualquier cosa"],
)
def test_match_criterion_rejects_unknown_names(unknown):
    assert _match_criterion(unknown) is None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_parse_review_accepts_a_valid_review_with_all_criteria():
    raw = review_json(
        [
            review_entry(SPELLING, "PASSED", "sin errores"),
            review_entry(COHERENCE, "FAILED", "no coincide", "alinear imagen"),
            review_entry(STRATEGY, "PASSED", "cta presente"),
            review_entry(BRAND, "PASSED", "tono correcto"),
            review_entry(RISK, "FAILED", "promesa absoluta", "retirar promesa"),
        ]
    )

    review = parse_review(raw)

    assert set(review) == set(SEMANTIC_CRITERIA)
    assert review[SPELLING].passed is True
    assert review[COHERENCE].passed is False
    assert review[COHERENCE].hallazgo == "alinear imagen"
    assert review[COHERENCE].criterio == COHERENCE


def test_parse_review_strips_evidence_and_normalizes_blank_findings():
    raw = review_json(
        [review_entry(SPELLING, "PASSED", "  evidencia  ", "   ")]
    )

    review = parse_review(raw)

    assert review[SPELLING].evidencia == "evidencia"
    assert review[SPELLING].hallazgo is None


def test_parse_review_accepts_markdown_fences():
    inner = review_json([review_entry(SPELLING, "PASSED", "ok")])
    raw = f"```json\n{inner}\n```"

    assert parse_review(raw)[SPELLING].passed is True


def test_parse_review_accepts_plain_fences_and_surrounding_prose():
    inner = review_json([review_entry(SPELLING, "FAILED", "error")])
    raw = f"Claro, aquí está el resultado:\n```\n{inner}\n```\nFin."

    assert parse_review(raw)[SPELLING].passed is False


def test_parse_review_ignores_unknown_criteria():
    raw = review_json(
        [
            review_entry("Criterio inventado", "FAILED", "x"),
            review_entry(SPELLING, "PASSED", "ok"),
        ]
    )

    review = parse_review(raw)

    assert list(review) == [SPELLING]


def test_parse_review_canonicalizes_the_reported_criterion():
    raw = review_json(
        [review_entry("coherencia texto-imagen-idea", "PASSED", "ok")]
    )

    assert COHERENCE in parse_review(raw)


def test_parse_review_rejects_a_review_without_known_criteria():
    raw = review_json([review_entry("Criterio inventado", "PASSED", "x")])

    with pytest.raises(GeminiResponseError, match="sin criterios"):
        parse_review(raw)


def test_parse_review_rejects_duplicate_criteria():
    raw = review_json(
        [
            review_entry(SPELLING, "PASSED", "uno"),
            review_entry(SPELLING, "FAILED", "dos"),
        ]
    )

    with pytest.raises(GeminiResponseError, match="duplicado"):
        parse_review(raw)


def test_parse_review_rejects_an_empty_checks_list():
    with pytest.raises(GeminiResponseError):
        parse_review('{"checks": []}')


def test_parse_review_rejects_more_than_twenty_checks():
    entries = [review_entry(SPELLING, "PASSED", f"e{i}") for i in range(21)]

    with pytest.raises(GeminiResponseError):
        parse_review(review_json(entries))


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "no hay json",
        "{",
        "{}",
        '{"checks": "no es lista"}',
        '{"checks": [{"criterio": "x"}]}',
        '{"checks": [{"criterio": "x", "resultado": "PASSED"}]}',
    ],
)
def test_parse_review_rejects_malformed_payloads(raw):
    with pytest.raises(GeminiResponseError):
        parse_review(raw)


def test_parse_review_rejects_lowercase_results():
    raw = review_json([review_entry(SPELLING, "passed", "ok")])

    with pytest.raises(GeminiResponseError):
        parse_review(raw)


def test_parse_review_rejects_blank_evidence():
    raw = review_json([review_entry(SPELLING, "PASSED", "   ")])

    with pytest.raises(GeminiResponseError, match="evidencia"):
        parse_review(raw)


def test_parse_review_accepts_evidence_at_the_character_limit():
    raw = review_json(
        [review_entry(SPELLING, "PASSED", "x" * MAX_EVIDENCE_CHARS)]
    )

    assert len(parse_review(raw)[SPELLING].evidencia) == MAX_EVIDENCE_CHARS


def test_parse_review_rejects_evidence_over_the_character_limit():
    raw = review_json(
        [review_entry(SPELLING, "PASSED", "x" * (MAX_EVIDENCE_CHARS + 1))]
    )

    with pytest.raises(GeminiResponseError):
        parse_review(raw)


def test_parse_review_rejects_findings_over_the_character_limit():
    raw = review_json(
        [
            review_entry(
                SPELLING,
                "FAILED",
                "ok",
                "x" * (MAX_EVIDENCE_CHARS + 1),
            )
        ]
    )

    with pytest.raises(GeminiResponseError):
        parse_review(raw)


def test_parse_review_ignores_extra_fields():
    entry = review_entry(SPELLING, "PASSED", "ok")
    entry["extra"] = "ignored"

    assert parse_review(review_json([entry]))[SPELLING].passed is True


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_build_prompt_contains_the_request_and_the_five_criteria(payload):
    prompt = build_prompt(make_request(payload))

    assert "Criterios a evaluar:" in prompt
    for criterion in SEMANTIC_CRITERIA:
        assert f"- {criterion}" in prompt
    assert payload["input"]["copy"]["caption"] in prompt
    assert payload["input"]["brief"]["objetivo"] in prompt
    assert payload["input"]["context_pack"]["empresa"] in prompt
    assert '"copy"' in prompt


def test_build_prompt_is_deterministic(payload):
    request = make_request(payload)

    assert build_prompt(request) == build_prompt(request)


def test_build_prompt_excludes_none_values(payload):
    payload["input"]["copy"]["hook"] = None
    payload["input"]["copy"]["cta"] = None
    payload["sent_at"] = None

    prompt = build_prompt(make_request(payload))

    assert '"hook"' not in prompt
    assert '"cta"' not in prompt
    assert '"sent_at"' not in prompt


def test_build_prompt_rejects_oversized_payloads(payload):
    payload["input"]["copy"]["caption"] = "x" * (MAX_PROMPT_CHARS + 1)

    with pytest.raises(GeminiResponseError, match="demasiado grande"):
        build_prompt(make_request(payload))


def test_build_prompt_allows_a_payload_below_the_limit(payload):
    assert len(build_prompt(make_request(payload))) < MAX_PROMPT_CHARS


# ---------------------------------------------------------------------------
# SDK adapter
# ---------------------------------------------------------------------------


def install_fake_genai(monkeypatch):
    """Install a fake `google.generativeai` module without touching the network."""

    state: dict = {"configure_calls": [], "models": [], "response_text": ""}

    class FakeGenerationConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.calls = []

        def generate_content(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            return types.SimpleNamespace(text=state["response_text"])

    fake_genai = types.ModuleType("google.generativeai")
    fake_genai.types = types.SimpleNamespace(GenerationConfig=FakeGenerationConfig)

    def configure(api_key):
        state["configure_calls"].append(api_key)

    def generative_model(**kwargs):
        model = FakeModel(**kwargs)
        state["models"].append(model)
        return model

    fake_genai.configure = configure
    fake_genai.GenerativeModel = generative_model

    fake_google = types.ModuleType("google")
    fake_google.generativeai = fake_genai

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    return state


def test_gemini_auditor_configures_the_sdk_with_bounded_deterministic_options(
    monkeypatch, payload
):
    state = install_fake_genai(monkeypatch)
    state["response_text"] = review_json(
        [review_entry(SPELLING, "PASSED", "ok")]
    )

    auditor = GeminiAuditor(
        api_key="sdk-key", model_name="gemini-test", timeout=12.5
    )
    review = auditor.review(make_request(payload))

    assert state["configure_calls"] == ["sdk-key"]
    model = state["models"][0]
    assert model.kwargs["model_name"] == "gemini-test"
    assert model.kwargs["system_instruction"] == SYSTEM_PROMPT
    assert model.kwargs["generation_config"].kwargs == {
        "temperature": 0.0,
        "top_p": 1.0,
        "candidate_count": 1,
        "max_output_tokens": 2048,
        "response_mime_type": "application/json",
    }
    prompt, options = model.calls[0]
    assert prompt.startswith("Criterios a evaluar:")
    assert options == {"request_options": {"timeout": 12.5}}
    assert review[SPELLING].passed is True


def test_gemini_auditor_sends_the_serialized_request(monkeypatch, payload):
    state = install_fake_genai(monkeypatch)
    state["response_text"] = review_json([review_entry(SPELLING, "PASSED", "ok")])

    GeminiAuditor("key", "model", 1).review(make_request(payload))

    prompt = state["models"][0].calls[0][0]
    assert payload["input"]["copy"]["caption"] in prompt
    assert payload["execution_id"] in prompt


def test_gemini_auditor_propagates_sdk_exceptions(monkeypatch, payload):
    state = install_fake_genai(monkeypatch)

    def fail(*args, **kwargs):
        raise TimeoutError("provider timeout")

    state["models_fail"] = True
    model_class = sys.modules["google.generativeai"].GenerativeModel

    def generative_model(**kwargs):
        model = model_class(**kwargs)
        model.generate_content = fail
        return model

    sys.modules["google.generativeai"].GenerativeModel = generative_model

    with pytest.raises(TimeoutError):
        GeminiAuditor("key", "model", 1).review(make_request(payload))


def test_gemini_auditor_propagates_parser_errors(monkeypatch, payload):
    state = install_fake_genai(monkeypatch)
    state["response_text"] = "not json"

    with pytest.raises(GeminiResponseError):
        GeminiAuditor("key", "model", 1).review(make_request(payload))


def test_gemini_auditor_configures_the_sdk_once_per_instance(monkeypatch):
    state = install_fake_genai(monkeypatch)

    GeminiAuditor("key-one", "model", 1)
    GeminiAuditor("key-two", "model", 1)

    assert state["configure_calls"] == ["key-one", "key-two"]
    assert len(state["models"]) == 2


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_build_gemini_auditor_returns_none_when_ai_is_disabled():
    settings = make_settings(ai_enabled=False)

    assert build_gemini_auditor(settings) is None


def test_build_gemini_auditor_returns_none_without_a_credential():
    settings = make_settings(AI_KEY="")

    assert settings.ai_key is None
    assert build_gemini_auditor(settings) is None


def test_build_gemini_auditor_uses_default_model_and_timeout(monkeypatch):
    created = {}

    class StubAuditor:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(gemini_module, "GeminiAuditor", StubAuditor)
    settings = make_settings()

    auditor = build_gemini_auditor(settings)

    assert isinstance(auditor, StubAuditor)
    assert created == {
        "api_key": "test-ai-key",
        "model_name": DEFAULT_AI_MODEL,
        "timeout": DEFAULT_AI_TIMEOUT_SECONDS,
    }


def test_build_gemini_auditor_passes_custom_model_and_timeout(monkeypatch):
    created = {}

    class StubAuditor:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(gemini_module, "GeminiAuditor", StubAuditor)
    settings = make_settings(ai_model="gemini-custom", ai_timeout_seconds=4.5)

    build_gemini_auditor(settings)

    assert created["model_name"] == "gemini-custom"
    assert created["timeout"] == 4.5


def test_build_gemini_auditor_unwraps_the_secret(monkeypatch):
    created = {}

    class StubAuditor:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(gemini_module, "GeminiAuditor", StubAuditor)
    settings = make_settings(AI_KEY=SecretStr("real-key-value"))

    build_gemini_auditor(settings)

    assert created["api_key"] == "real-key-value"


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def test_reconciliation_lets_ai_fail_a_deterministic_pass():
    deterministic = [make_outcome(SPELLING, weight=10, route="AG-COP")]
    review = {SPELLING: AiCheck(SPELLING, False, "falta claridad", "reescribir")}

    [result] = reconcile_outcomes(deterministic, review)

    assert result.passed is False
    assert result.evidencia == "falta claridad"
    assert result.hallazgo == "reescribir"
    assert result.weight == 10
    assert result.severe is False
    assert result.route == "AG-COP"


def test_reconciliation_uses_ai_evidence_as_finding_when_no_finding_is_given():
    deterministic = [make_outcome(STRATEGY, weight=16, route="AG-EST")]
    review = {STRATEGY: AiCheck(STRATEGY, False, "no hay CTA")}

    [result] = reconcile_outcomes(deterministic, review)

    assert result.hallazgo == "no hay CTA"


def test_reconciliation_lets_ai_replace_evidence_on_a_deterministic_pass():
    deterministic = [make_outcome(SPELLING)]
    review = {SPELLING: AiCheck(SPELLING, True, "revisión semántica correcta")}

    [result] = reconcile_outcomes(deterministic, review)

    assert result.passed is True
    assert result.evidencia == "revisión semántica correcta"


def test_reconciliation_never_turns_a_deterministic_failure_into_a_pass():
    deterministic = [
        make_outcome(STRATEGY, passed=False, weight=16, route="AG-EST",
                     evidence="sin CTA", finding="añadir CTA")
    ]
    review = {STRATEGY: AiCheck(STRATEGY, True, "parece correcto")}

    [result] = reconcile_outcomes(deterministic, review)

    assert result.passed is False
    assert result.evidencia == "sin CTA | Gemini no detectó problemas adicionales."
    assert result.hallazgo == "añadir CTA"
    assert result.weight == 16


def test_reconciliation_appends_ai_evidence_to_a_deterministic_failure():
    deterministic = [
        make_outcome(SPELLING, passed=False, evidence="espacios dobles")
    ]
    review = {SPELLING: AiCheck(SPELLING, False, "además falta puntuación", "corregir")}

    [result] = reconcile_outcomes(deterministic, review)

    assert result.passed is False
    assert result.evidencia == "espacios dobles | Gemini: además falta puntuación"
    assert result.hallazgo == "corregir"


def test_reconciliation_does_not_duplicate_ai_evidence_already_present():
    deterministic = [
        make_outcome(SPELLING, passed=False, evidence="falta claridad en la frase")
    ]
    review = {SPELLING: AiCheck(SPELLING, False, "falta claridad")}

    [result] = reconcile_outcomes(deterministic, review)

    assert result.evidencia == "falta claridad en la frase"


def test_reconciliation_uses_ai_finding_when_deterministic_failure_has_none():
    deterministic = [
        make_outcome(SPELLING, passed=False, evidence="error", finding=None)
    ]
    review = {SPELLING: AiCheck(SPELLING, False, "detalle", "hallazgo IA")}

    [result] = reconcile_outcomes(deterministic, review)

    assert result.hallazgo == "hallazgo IA"


def test_reconciliation_uses_ai_evidence_if_ai_approves_a_failure_without_finding():
    deterministic = [
        make_outcome(SPELLING, passed=False, evidence="error", finding=None)
    ]
    review = {SPELLING: AiCheck(SPELLING, True, "parece correcto")}

    [result] = reconcile_outcomes(deterministic, review)

    assert result.hallazgo == "parece correcto"


def test_reconciliation_leaves_outcomes_without_ai_results_untouched():
    deterministic = [make_outcome(SPELLING), make_outcome(COHERENCE)]

    reconciled = reconcile_outcomes(deterministic, {})

    assert reconciled[0] is deterministic[0]
    assert reconciled[1] is deterministic[1]


def test_reconciliation_ignores_ai_results_for_non_semantic_checks():
    veracity = make_outcome(
        "Veracidad de Datos Comercial",
        passed=False,
        weight=24,
        severe=True,
        evidence="dato inventado",
    )
    limits = make_outcome(
        "Límites Técnicos de Instagram", passed=True, weight=10, severe=True
    )
    review = {
        veracity.criterio: AiCheck(veracity.criterio, True, "la IA lo aprueba"),
        limits.criterio: AiCheck(limits.criterio, False, "la IA lo rechaza"),
    }

    reconciled = reconcile_outcomes([veracity, limits], review)

    assert reconciled[0] is veracity
    assert reconciled[1] is limits
    assert reconciled[0].passed is False
    assert reconciled[1].passed is True


def test_reconciliation_preserves_order_and_copies_metadata():
    sources = [DataSource(source="perfil_negocio.facts", ref="FACT-001")]
    deterministic = [
        make_outcome(SPELLING, weight=10, route="AG-COP"),
        make_outcome(COHERENCE, weight=16, route="AG-BOR"),
        make_outcome("Veracidad de Datos Comercial", weight=24, severe=True,
                     route="AG-COP", sources=sources),
        make_outcome(STRATEGY, weight=16, route="AG-EST"),
        make_outcome(BRAND, weight=12, route="AG-COP"),
        make_outcome(RISK, weight=12, severe=True, route="AG-COP"),
        make_outcome("Límites Técnicos de Instagram", weight=10, severe=True),
    ]

    reconciled = reconcile_outcomes(deterministic, all_semantic_pass_review())

    assert [item.criterio for item in reconciled] == [
        item.criterio for item in deterministic
    ]
    assert [item.weight for item in reconciled] == [10, 16, 24, 16, 12, 12, 10]
    assert [item.route for item in reconciled] == [
        "AG-COP", "AG-BOR", "AG-COP", "AG-EST", "AG-COP", "AG-COP", "AG-COP",
    ]
    assert [item.severe for item in reconciled] == [
        False, False, True, False, False, True, True,
    ]
    assert reconciled[2].data_sources == sources


def test_reconciliation_does_not_mutate_the_deterministic_outcomes():
    deterministic = [make_outcome(SPELLING), make_outcome(STRATEGY, passed=False)]
    baseline = [replace(item) for item in deterministic]
    review = {
        SPELLING: AiCheck(SPELLING, False, "nuevo problema"),
        STRATEGY: AiCheck(STRATEGY, True, "sin problemas"),
    }

    reconcile_outcomes(deterministic, review)

    assert deterministic == baseline


def test_reconciliation_of_real_run_checks_keeps_the_seven_criteria(payload):
    deterministic = run_checks(make_request(payload))

    reconciled = reconcile_outcomes(deterministic, all_semantic_pass_review())

    assert [item.criterio for item in reconciled] == [
        item.criterio for item in deterministic
    ]
    assert len(reconciled) == 7


# ---------------------------------------------------------------------------
# Service integration
# ---------------------------------------------------------------------------


def test_ai_disabled_never_calls_the_injected_auditor(payload):
    request = make_request(payload)
    settings = make_settings(ai_enabled=False)
    fake = FakeAuditor({SPELLING: AiCheck(SPELLING, False, "should not be used")})

    response = run_audit(request, settings, ai_auditor=fake)

    assert fake.calls == []
    assert response.output.score == 100
    assert response.output.veredicto == "APROBADO"


def test_ai_disabled_response_matches_the_deterministic_baseline(payload):
    request = make_request(payload)
    settings = make_settings(ai_enabled=False)
    fake = FakeAuditor({SPELLING: AiCheck(SPELLING, False, "different")})

    baseline = run_audit(request, settings)
    with_fake = run_audit(request, settings, ai_auditor=fake)

    assert with_fake.output.checks == baseline.output.checks
    assert with_fake.output.score == baseline.output.score
    assert with_fake.output.veredicto == baseline.output.veredicto
    assert with_fake.output.hallazgos == baseline.output.hallazgos
    assert with_fake.output.return_to == baseline.output.return_to
    assert with_fake.data_sources == baseline.data_sources
    assert with_fake.warnings == baseline.warnings


def test_ai_enabled_calls_the_auditor_exactly_once(payload):
    request = make_request(payload)
    fake = FakeAuditor(all_semantic_pass_review())

    response = run_audit(request, make_settings(), ai_auditor=fake)

    assert len(fake.calls) == 1
    assert fake.calls[0] is request
    assert response.output.score == 100


def test_ai_can_add_a_failure_and_change_the_verdict(payload):
    request = make_request(payload)
    fake = FakeAuditor(
        {COHERENCE: AiCheck(COHERENCE, False, "imagen y copy divergen", "alinear")}
    )

    response = run_audit(request, make_settings(), ai_auditor=fake)
    output = response.output

    assert output.score == 84
    assert output.veredicto == "RECHAZADO"
    assert response.status == "rejected"
    assert output.return_to == "AG-BOR"
    assert output.approval_id is None
    assert output.content_hash is None
    assert output.content_signature is None
    assert any(item.criterio == COHERENCE for item in output.hallazgos)
    coherence = next(item for item in output.checks if item.criterio == COHERENCE)
    assert coherence.resultado == "FAILED"
    assert coherence.evidencia == "imagen y copy divergen"


def test_ai_brand_failure_keeps_the_verdict_approved_above_threshold(payload):
    request = make_request(payload)
    fake = FakeAuditor(
        {BRAND: AiCheck(BRAND, False, "tono demasiado agresivo", "suavizar tono")}
    )

    response = run_audit(request, make_settings(), ai_auditor=fake)
    output = response.output

    assert output.score == 88
    assert output.veredicto == "APROBADO"
    assert response.status == "completed"
    assert output.hallazgos
    assert output.hallazgos[0].severidad == "media"
    assert output.approval_id is not None


def test_ai_risk_failure_is_severe_and_rejects_above_threshold(payload):
    request = make_request(payload)
    fake = FakeAuditor(
        {RISK: AiCheck(RISK, False, "promesa absoluta", "eliminar promesa")}
    )

    response = run_audit(request, make_settings(), ai_auditor=fake)
    output = response.output

    assert output.score == 88
    assert output.veredicto == "RECHAZADO"
    assert response.status == "rejected"
    assert output.return_to == "AG-COP"
    assert any(item.severidad == "alta" for item in output.hallazgos)


def test_ai_results_for_objective_checks_are_ignored(payload):
    request = make_request(payload)
    fake = FakeAuditor(
        {
            "Veracidad de Datos Comercial": AiCheck(
                "Veracidad de Datos Comercial", False, "inventado"
            ),
            "Límites Técnicos de Instagram": AiCheck(
                "Límites Técnicos de Instagram", False, "inválido"
            ),
        }
    )

    response = run_audit(request, make_settings(), ai_auditor=fake)

    assert response.output.score == 100
    assert response.output.veredicto == "APROBADO"
    assert all(item.resultado == "PASSED" for item in response.output.checks)


def test_ai_cannot_override_a_deterministic_failure(payload):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."
    request = make_request(payload)
    fake = FakeAuditor({STRATEGY: AiCheck(STRATEGY, True, "parece alineado")})

    response = run_audit(request, make_settings(), ai_auditor=fake)
    output = response.output

    assert output.score == 68
    assert output.veredicto == "RECHAZADO"
    assert output.return_to == "AG-EST"
    strategy = next(item for item in output.checks if item.criterio == STRATEGY)
    assert strategy.resultado == "FAILED"
    assert "Gemini no detectó problemas adicionales" in strategy.evidencia


def test_ai_failure_falls_back_to_deterministic_and_warns(payload):
    request = make_request(payload)
    fake = FakeAuditor(error=TimeoutError("provider timeout"))

    response = run_audit(request, make_settings(), ai_auditor=fake)

    assert response.output.score == 100
    assert response.output.veredicto == "APROBADO"
    assert response.warnings == [
        "La revisión con Gemini no está disponible; se usó la auditoría determinista."
    ]
    assert "provider timeout" not in response.warnings[0]


def test_malformed_ai_review_falls_back_to_deterministic(payload):
    request = make_request(payload)
    fake = FakeAuditor(error=GeminiResponseError("respuesta sin objeto JSON"))

    response = run_audit(request, make_settings(), ai_auditor=fake)

    assert response.output.score == 100
    assert "Gemini no está disponible" in response.warnings[0]


def test_ai_fallback_warning_contains_no_prompt_or_credential(payload):
    request = make_request(payload)
    fake = FakeAuditor(error=RuntimeError("leaked test-ai-key and full prompt"))

    response = run_audit(request, make_settings(), ai_auditor=fake)

    warning = response.warnings[0]
    assert "test-ai-key" not in warning
    assert payload["input"]["copy"]["caption"] not in warning


def test_ai_enabled_without_credential_uses_deterministic_audit(payload):
    request = make_request(payload)
    settings = make_settings(AI_KEY="")

    response = run_audit(request, settings)

    assert response.output.score == 100
    assert response.warnings == [
        "AUDITOR_AI_ENABLED está activo pero AI_KEY no está configurada; "
        "se usó la auditoría determinista."
    ]


def test_warning_order_is_dev_key_then_ai_fallback(payload):
    request = make_request(payload)
    settings = make_settings(signing_key=None)
    fake = FakeAuditor(error=TimeoutError("timeout"))

    response = run_audit(request, settings, ai_auditor=fake)

    assert len(response.warnings) == 2
    assert "AUDITOR_SIGNING_KEY" in response.warnings[0]
    assert "Gemini" in response.warnings[1]


def test_ai_review_with_missing_criteria_leaves_deterministic_results(payload):
    request = make_request(payload)
    fake = FakeAuditor({SPELLING: AiCheck(SPELLING, True, "ok")})

    response = run_audit(request, make_settings(), ai_auditor=fake)
    checks = {item.criterio: item for item in response.output.checks}

    assert checks[SPELLING].evidencia == "ok"
    assert checks[COHERENCE].evidencia != "ok"
    assert response.output.score == 100


def test_ai_approval_does_not_change_hash_or_signature(payload):
    request = make_request(payload)
    deterministic = run_audit(request, make_settings(ai_enabled=False))
    assisted = run_audit(
        request, make_settings(), ai_auditor=FakeAuditor(all_semantic_pass_review())
    )

    assert assisted.output.content_hash == deterministic.output.content_hash
    assert assisted.output.content_signature == deterministic.output.content_signature
    assert assisted.output.veredicto == "APROBADO"


def test_ai_enabled_response_keeps_the_full_contract_shape(payload):
    request = make_request(payload)
    fake = FakeAuditor(all_semantic_pass_review())

    dumped = run_audit(request, make_settings(), ai_auditor=fake).model_dump(mode="json")

    assert set(dumped) == {
        "contract_version", "execution_id", "agent_id", "status", "output",
        "data_sources", "warnings", "error", "started_at", "finished_at",
    }
    assert set(dumped["output"]) == {
        "veredicto", "score", "checks", "hallazgos", "return_to",
        "approval_id", "content_hash", "content_signature",
    }
    assert [item["criterio"] for item in dumped["output"]["checks"]] == [
        "Ortografía, Gramática y Claridad",
        "Coherencia Texto–Imagen–Idea",
        "Veracidad de Datos Comercial",
        "Alineación Estratégica y Audiencia",
        "Voz, Tono y Reglas de Marca",
        "Riesgo y Afirmaciones No Sustentadas",
        "Límites Técnicos de Instagram",
    ]


def test_configured_ai_key_never_appears_in_response_or_trace(
    client, payload, trace_path, monkeypatch
):
    secret = "sk-test-do-not-leak-987654321"
    monkeypatch.setenv("AUDITOR_AI_ENABLED", "true")
    monkeypatch.setenv("AI_KEY", secret)
    monkeypatch.setattr(
        "app.service.build_gemini_auditor",
        lambda settings: FakeAuditor(all_semantic_pass_review()),
    )

    response = client.post("/invoke", json=payload)

    assert response.status_code == 200
    assert secret not in response.text
    trace = Path(trace_path).read_text(encoding="utf-8")
    assert secret not in trace
    assert "AI_KEY=" not in trace


def test_ai_is_not_called_by_the_api_when_disabled(client, payload, monkeypatch):
    called = {"count": 0}

    def fail(settings):
        called["count"] += 1
        raise AssertionError("Gemini must not be built when AI is disabled")

    monkeypatch.setattr("app.service.build_gemini_auditor", fail)

    response = client.post("/invoke", json=payload)

    assert response.status_code == 200
    assert called["count"] == 0
