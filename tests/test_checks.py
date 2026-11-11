"""Unit tests for each of the seven audit checks."""

from __future__ import annotations

from app.checks import run_checks
from app.models import AuditRequest

CRITERIA = {
    "spelling": "Ortografía, Gramática y Claridad",
    "coherence": "Coherencia Texto–Imagen–Idea",
    "veracity": "Veracidad de Datos Comercial",
    "strategy": "Alineación Estratégica y Audiencia",
    "brand": "Voz, Tono y Reglas de Marca",
    "risk": "Riesgo y Afirmaciones No Sustentadas",
    "limits": "Límites Técnicos de Instagram",
}


def outcome(payload, name):
    request = AuditRequest.model_validate(payload)
    return {item.criterio: item for item in run_checks(request)}[CRITERIA[name]]


def test_spelling_passes_clean_caption(payload):
    assert outcome(payload, "spelling").passed is True


def test_spelling_detects_placeholder_and_unbalanced_delimiter(payload):
    payload["input"]["copy"]["caption"] = "Visita  la tienda TODO (deportiva"

    result = outcome(payload, "spelling")

    assert result.passed is False
    assert "marcador" in result.evidencia
    assert "delimitadores" in result.evidencia


def test_coherence_fails_without_alt_text(payload):
    payload["input"]["borrador"]["alt_text"] = ""

    result = outcome(payload, "coherence")

    assert result.passed is False
    assert result.route == "AG-BOR"


def test_coherence_fails_when_caption_is_unrelated(payload):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."

    result = outcome(payload, "coherence")

    assert result.passed is False


def test_veracity_fails_for_invented_percentage(payload):
    payload["input"]["copy"]["caption"] = (
        "¡Aprovecha 50% OFF en ropa deportiva! Visita la tienda."
    )

    result = outcome(payload, "veracity")

    assert result.passed is False
    assert result.severe is True
    assert "50%" in result.evidencia


def test_veracity_fails_for_unknown_fact_reference(payload):
    payload["input"]["copy"]["facts_used"] = ["FACT-999"]

    result = outcome(payload, "veracity")

    assert result.passed is False
    assert "FACT-999" in result.evidencia


def test_veracity_skips_fact_catalog_check_when_catalog_is_absent(payload):
    payload["input"]["context_pack"]["facts"] = []
    payload["input"]["copy"]["facts_used"] = ["FACT-012"]

    assert outcome(payload, "veracity").passed is True


def test_strategy_fails_without_cta_or_objective_overlap(payload):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."

    result = outcome(payload, "strategy")

    assert result.passed is False
    assert result.route == "AG-EST"


def test_strategy_passes_with_objective_overlap(payload):
    payload["input"]["copy"]["caption"] = "La tienda tiene nueva ropa para ti."

    assert outcome(payload, "strategy").passed is True


def test_brand_fails_for_prohibited_term(payload):
    payload["input"]["copy"]["caption"] = (
        "¡Compra barato en la tienda! 20% OFF en ropa deportiva."
    )

    result = outcome(payload, "brand")

    assert result.passed is False
    assert "barato" in result.evidencia


def test_brand_fails_for_sustained_upper_case(payload):
    payload["input"]["copy"]["caption"] = "COMPRA YA EN LA TIENDA DEPORTIVA"

    assert outcome(payload, "brand").passed is False


def test_risk_fails_for_unsupported_absolute_claim(payload):
    payload["input"]["copy"]["caption"] = (
        "¡Compra ya! Siempre 20% OFF en ropa deportiva."
    )

    result = outcome(payload, "risk")

    assert result.passed is False
    assert result.severe is True
    assert "siempre" in result.evidencia


def test_risk_allows_claim_supported_by_context(payload):
    payload["input"]["context_pack"]["facts"] = [
        {"id": "FACT-012", "texto": "Siempre 20% OFF en ropa deportiva"}
    ]

    assert outcome(payload, "risk").passed is True


def test_limits_fail_for_long_caption(payload):
    payload["input"]["copy"]["caption"] = "Visita la tienda. " + ("x" * 2300)

    result = outcome(payload, "limits")

    assert result.passed is False
    assert result.severe is True
    assert "2200" in result.evidencia


def test_limits_fail_for_too_many_hashtags(payload):
    payload["input"]["copy"]["hashtags"] = [f"#tag{i}" for i in range(31)]

    assert outcome(payload, "limits").passed is False


def test_limits_fail_for_malformed_hashtag(payload):
    payload["input"]["copy"]["hashtags"] = ["#Deporte", "Deporte"]

    assert outcome(payload, "limits").passed is False


def test_limits_fail_for_unsupported_image_format(payload):
    payload["input"]["borrador"]["asset_url"] = "https://cdn.example.com/post.gif"

    result = outcome(payload, "limits")

    assert result.passed is False
    assert "gif" in result.evidencia
