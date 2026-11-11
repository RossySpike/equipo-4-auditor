"""Score, verdict, routing, source deduplication, and response construction."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.checks import CheckOutcome
from app.config import DEVELOPMENT_SIGNING_KEY, Settings
from app.models import AuditRequest, DataSource
from app.service import _dedupe_sources, _select_return_to, run_audit
from app.signing import verify_content_signature


def make_settings(**overrides) -> Settings:
    values = {"signing_key": "test-signing-key", "approval_threshold": 85}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_request(payload) -> AuditRequest:
    return AuditRequest.model_validate(payload)


def outcome(route, passed, *, weight=10, severe=False, sources=None):
    return CheckOutcome(
        criterio=f"criterio-{route}-{weight}-{severe}",
        weight=weight,
        severe=severe,
        route=route,
        passed=passed,
        evidencia="evidence",
        data_sources=sources or [],
    )


# ---------------------------------------------------------------------------
# Return routing
# ---------------------------------------------------------------------------


def test_routing_prefers_strategy_over_everything_else():
    outcomes = [
        outcome("AG-COP", False),
        outcome("AG-BOR", False),
        outcome("AG-EST", False),
    ]

    assert _select_return_to(outcomes) == "AG-EST"


def test_routing_prefers_draft_over_copy():
    assert _select_return_to([outcome("AG-COP", False), outcome("AG-BOR", False)]) == "AG-BOR"


def test_routing_falls_back_to_copy_for_copy_failures():
    assert _select_return_to([outcome("AG-COP", False)]) == "AG-COP"


def test_routing_ignores_passing_outcomes():
    outcomes = [outcome("AG-EST", True), outcome("AG-BOR", True), outcome("AG-COP", True)]

    assert _select_return_to(outcomes) == "AG-COP"


def test_routing_defaults_to_copy_when_nothing_failed():
    assert _select_return_to([]) == "AG-COP"


# ---------------------------------------------------------------------------
# Source deduplication
# ---------------------------------------------------------------------------


def test_sources_are_deduplicated_across_outcomes_preserving_order():
    first = DataSource(source="perfil_negocio.promociones", ref="PROMO-1")
    second = DataSource(source="perfil_negocio.facts", ref="FACT-1")
    outcomes = [
        outcome("AG-COP", True, sources=[first, second]),
        outcome("AG-COP", True, sources=[first]),
        outcome("AG-BOR", True, sources=[second]),
    ]

    assert _dedupe_sources(outcomes) == [first, second]


def test_sources_from_no_outcomes_is_empty():
    assert _dedupe_sources([]) == []
    assert _dedupe_sources([outcome("AG-COP", True)]) == []


def test_sources_with_same_ref_but_different_source_are_kept():
    outcomes = [
        outcome("AG-COP", True, sources=[DataSource(source="a", ref="X")]),
        outcome("AG-COP", True, sources=[DataSource(source="b", ref="X")]),
    ]

    assert len(_dedupe_sources(outcomes)) == 2


# ---------------------------------------------------------------------------
# Verdict and score
# ---------------------------------------------------------------------------


def test_clean_content_scores_one_hundred_and_is_approved(payload):
    response = run_audit(make_request(payload), make_settings())

    assert response.status == "completed"
    assert response.output.veredicto == "APROBADO"
    assert response.output.score == 100
    assert response.output.hallazgos == []
    assert response.output.return_to is None
    assert response.error is None


def test_each_non_severe_failure_subtracts_its_weight(payload):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."

    response = run_audit(make_request(payload), make_settings())

    assert response.output.score == 68
    assert response.output.veredicto == "RECHAZADO"
    assert response.status == "rejected"


def test_severe_failure_rejects_even_when_score_is_above_threshold(payload):
    payload["input"]["copy"]["caption"] = (
        "¡Compra ya! Siempre 20% OFF en ropa deportiva."
    )

    response = run_audit(make_request(payload), make_settings(approval_threshold=50))

    assert response.output.score == 88
    assert response.output.veredicto == "RECHAZADO"
    assert any(item.severidad == "alta" for item in response.output.hallazgos)


def test_score_is_floored_at_zero(payload):
    payload["input"]["copy"]["caption"] = (
        "TODO ( $9.999 50% OFF Siempre garantizado barato " + "X" * 2300
    )
    payload["input"]["copy"]["hashtags"] = ["bad"] + [f"#t{i}" for i in range(31)]
    payload["input"]["borrador"]["asset_url"] = "https://cdn.example.com/a.gif"
    payload["input"]["borrador"]["alt_text"] = ""
    payload["input"]["copy"]["facts_used"] = ["FACT-999"]

    response = run_audit(make_request(payload), make_settings())

    assert response.output.score >= 0
    assert response.output.veredicto == "RECHAZADO"
    assert len(response.output.hallazgos) == len(response.output.checks)


def test_score_equal_to_threshold_is_approved_when_no_severe_failure(payload):
    # A spelling failure costs 10 points, leaving exactly 90.
    payload["input"]["copy"]["caption"] = "Visita  la tienda con 20% OFF."

    response = run_audit(make_request(payload), make_settings(approval_threshold=90))

    assert response.output.score == 90
    assert response.output.veredicto == "APROBADO"
    assert response.status == "completed"
    assert any(item["resultado"] == "FAILED" for item in
               [check.model_dump() for check in response.output.checks])


def test_score_one_point_below_threshold_is_rejected(payload):
    payload["input"]["copy"]["caption"] = "Visita  la tienda con 20% OFF."

    response = run_audit(make_request(payload), make_settings(approval_threshold=91))

    assert response.output.score == 90
    assert response.output.veredicto == "RECHAZADO"


@pytest.mark.parametrize("threshold", [0, 50, 85, 100])
def test_threshold_is_respected_for_clean_content(payload, threshold):
    response = run_audit(make_request(payload), make_settings(approval_threshold=threshold))

    assert response.output.veredicto == "APROBADO"


def test_hallazgo_severity_mapping_is_alta_for_severe_and_media_for_the_rest(payload):
    payload["input"]["copy"]["caption"] = "Visita  la tienda. Siempre 20% OFF."

    response = run_audit(make_request(payload), make_settings())
    severities = {item.criterio: item.severidad for item in response.output.hallazgos}

    assert severities["Ortografía, Gramática y Claridad"] == "media"
    assert severities["Riesgo y Afirmaciones No Sustentadas"] == "alta"
    assert "baja" not in severities.values()


def test_one_hallazgo_per_failed_check(payload):
    payload["input"]["copy"]["caption"] = "Visita  la tienda barato. Siempre 50% OFF."

    response = run_audit(make_request(payload), make_settings())
    failed = [item for item in response.output.checks if item.resultado == "FAILED"]

    assert len(response.output.hallazgos) == len(failed)
    assert {item.criterio for item in response.output.hallazgos} == {
        item.criterio for item in failed
    }


def test_hallazgo_detalle_falls_back_to_evidence(payload):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."

    response = run_audit(make_request(payload), make_settings())

    assert all(item.detalle for item in response.output.hallazgos)


# ---------------------------------------------------------------------------
# Approval fields and signing
# ---------------------------------------------------------------------------


def test_approval_fields_are_generated_only_for_approved_content(payload):
    approved = run_audit(make_request(payload), make_settings())
    assert approved.output.approval_id
    assert approved.output.content_hash
    assert approved.output.content_signature

    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."
    rejected = run_audit(make_request(payload), make_settings())
    assert rejected.output.approval_id is None
    assert rejected.output.content_hash is None
    assert rejected.output.content_signature is None


def test_signature_uses_the_configured_signing_key(payload):
    response = run_audit(make_request(payload), make_settings(signing_key="my-key"))

    assert verify_content_signature(
        response.output.content_hash,
        response.output.content_signature,
        "my-key",
    )
    assert not verify_content_signature(
        response.output.content_hash,
        response.output.content_signature,
        "other-key",
    )


def test_signature_uses_the_development_key_when_not_configured(payload):
    response = run_audit(make_request(payload), make_settings(signing_key=None))

    assert verify_content_signature(
        response.output.content_hash,
        response.output.content_signature,
        DEVELOPMENT_SIGNING_KEY,
    )


def test_timestamps_are_utc_aware_and_ordered(payload):
    response = run_audit(make_request(payload), make_settings())

    assert response.started_at.tzinfo is not None
    assert response.finished_at.tzinfo is not None
    assert response.started_at <= response.finished_at
    assert response.started_at.utcoffset().total_seconds() == 0
    assert response.finished_at.utcoffset().total_seconds() == 0


def test_response_echoes_the_execution_id_and_uses_contract_defaults(payload):
    payload["execution_id"] = "EXE-CUSTOM-42"

    response = run_audit(make_request(payload), make_settings())

    assert response.execution_id == "EXE-CUSTOM-42"
    assert response.contract_version == "1.0"
    assert response.agent_id == "AG-AUD"


# ---------------------------------------------------------------------------
# Warnings and data sources
# ---------------------------------------------------------------------------


def test_development_key_produces_the_documented_warning(payload):
    response = run_audit(make_request(payload), make_settings(signing_key=None))

    assert response.warnings == [
        "AUDITOR_SIGNING_KEY no está configurada; se usa una clave de desarrollo."
    ]


def test_configured_key_produces_no_warning(payload):
    response = run_audit(make_request(payload), make_settings())

    assert response.warnings == []


def test_data_sources_are_reported_for_verified_commercial_claims(payload):
    response = run_audit(make_request(payload), make_settings())

    assert response.data_sources == [
        DataSource(source="perfil_negocio.promociones", ref="PROMO-003"),
        DataSource(source="perfil_negocio.facts", ref="FACT-012"),
    ]


def test_data_sources_are_empty_without_commercial_claims(payload):
    payload["input"]["copy"]["caption"] = "Visita la tienda deportiva."
    payload["input"]["copy"]["facts_used"] = []

    response = run_audit(make_request(payload), make_settings())

    assert response.data_sources == []


def test_rejected_content_still_reports_verified_sources(payload):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."

    response = run_audit(make_request(payload), make_settings())

    assert response.data_sources == []


# ---------------------------------------------------------------------------
# Checks in the response
# ---------------------------------------------------------------------------


def test_response_always_reports_seven_checks_with_valid_results(payload):
    for caption in (
        payload["input"]["copy"]["caption"],
        "La nueva colección ya está aquí.",
        "Visita  la tienda TODO. Siempre 50% OFF.",
    ):
        payload["input"]["copy"]["caption"] = caption
        response = run_audit(make_request(payload), make_settings())

        assert len(response.output.checks) == 7
        assert all(item.resultado in {"PASSED", "FAILED"} for item in response.output.checks)
        assert all(item.evidencia for item in response.output.checks)


def test_response_is_a_pydantic_object_with_serializable_timestamps(payload):
    response = run_audit(make_request(payload), make_settings())

    dumped = response.model_dump(mode="json")
    started = datetime.fromisoformat(dumped["started_at"])

    assert started.tzinfo is not None
    assert dumped["error"] is None
