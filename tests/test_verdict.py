"""Verdict, scoring, severity, and return-routing tests."""

from __future__ import annotations


def invoke(client, payload):
    response = client.post("/invoke", json=payload)
    assert response.status_code == 200
    return response.json()


def test_approves_clean_content(client, payload):
    body = invoke(client, payload)
    output = body["output"]

    assert body["status"] == "completed"
    assert output["veredicto"] == "APROBADO"
    assert output["score"] == 100
    assert output["return_to"] is None
    assert output["approval_id"] is not None
    assert output["content_hash"] is not None


def test_severe_risk_failure_forces_rejection_above_threshold(client, payload):
    payload["input"]["copy"]["caption"] = (
        "¡Compra ya! Siempre 20% OFF en ropa deportiva."
    )

    output = invoke(client, payload)["output"]

    assert output["score"] == 88
    assert output["veredicto"] == "RECHAZADO"
    assert output["return_to"] == "AG-COP"
    assert any(item["severidad"] == "alta" for item in output["hallazgos"])


def test_severe_failure_rejects_even_with_low_threshold(client, payload, monkeypatch):
    monkeypatch.setenv("AUDITOR_APPROVAL_THRESHOLD", "50")
    payload["input"]["copy"]["caption"] = (
        "¡Compra ya! Siempre 20% OFF en ropa deportiva."
    )

    output = invoke(client, payload)["output"]

    assert output["score"] == 88
    assert output["veredicto"] == "RECHAZADO"


def test_score_below_configured_threshold_rejects(client, payload, monkeypatch):
    monkeypatch.setenv("AUDITOR_APPROVAL_THRESHOLD", "95")
    payload["input"]["copy"]["caption"] = (
        "Visita  la tienda con 20% OFF en ropa deportiva."
    )

    output = invoke(client, payload)["output"]

    assert output["score"] == 90
    assert output["veredicto"] == "RECHAZADO"
    assert output["hallazgos"]


def test_strategy_failure_routes_to_strategist(client, payload):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."

    output = invoke(client, payload)["output"]

    assert output["veredicto"] == "RECHAZADO"
    assert output["score"] == 68
    assert output["return_to"] == "AG-EST"


def test_coherence_failure_routes_to_draft_creator(client, payload):
    payload["input"]["copy"]["caption"] = "Compra ya."

    output = invoke(client, payload)["output"]

    assert output["veredicto"] == "RECHAZADO"
    assert output["score"] == 84
    assert output["return_to"] == "AG-BOR"


def test_copy_failures_route_to_copywriter(client, payload):
    payload["input"]["copy"]["caption"] = (
        "Visita  la tienda barato con 20% OFF en ropa deportiva."
    )

    output = invoke(client, payload)["output"]

    assert output["veredicto"] == "RECHAZADO"
    assert output["score"] == 78
    assert output["return_to"] == "AG-COP"


def test_rejected_content_has_no_approval_fields(client, payload):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."

    output = invoke(client, payload)["output"]

    assert output["approval_id"] is None
    assert output["content_hash"] is None
    assert output["content_signature"] is None
