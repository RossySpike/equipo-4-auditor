"""Contract-level API tests."""

from __future__ import annotations

import re


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "agent_id": "AG-AUD"}


def test_valid_invocation_returns_full_envelope(client, payload):
    response = client.post("/invoke", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "1.0"
    assert body["execution_id"] == "EXE-0007"
    assert body["agent_id"] == "AG-AUD"
    assert body["status"] == "completed"
    assert body["error"] is None
    assert body["warnings"] == []
    assert body["started_at"] <= body["finished_at"]

    output = body["output"]
    assert output["veredicto"] == "APROBADO"
    assert output["score"] == 100
    assert output["hallazgos"] == []
    assert output["return_to"] is None
    assert re.fullmatch(r"APP-\d{8}-[0-9a-f]{8}", output["approval_id"])
    assert re.fullmatch(r"[0-9a-f]{64}", output["content_hash"])
    assert re.fullmatch(r"[0-9a-f]{64}", output["content_signature"])


def test_reports_the_seven_checks_in_order(client, payload):
    response = client.post("/invoke", json=payload)
    checks = response.json()["output"]["checks"]

    assert [check["criterio"] for check in checks] == [
        "Ortografía, Gramática y Claridad",
        "Coherencia Texto–Imagen–Idea",
        "Veracidad de Datos Comercial",
        "Alineación Estratégica y Audiencia",
        "Voz, Tono y Reglas de Marca",
        "Riesgo y Afirmaciones No Sustentadas",
        "Límites Técnicos de Instagram",
    ]
    assert {check["resultado"] for check in checks} == {"PASSED"}


def test_reports_commercial_sources(client, payload):
    body = client.post("/invoke", json=payload).json()

    assert {"source": "perfil_negocio.promociones", "ref": "PROMO-003"} in body["data_sources"]
    assert {"source": "perfil_negocio.facts", "ref": "FACT-012"} in body["data_sources"]


def test_rejects_unsupported_contract_version(client, payload):
    payload["contract_version"] = "2.0"

    response = client.post("/invoke", json=payload)

    assert response.status_code == 422


def test_rejects_wrong_agent_id(client, payload):
    payload["agent_id"] = "AG-ORQ"

    response = client.post("/invoke", json=payload)

    assert response.status_code == 422


def test_rejects_blank_execution_id(client, payload):
    payload["execution_id"] = "   "

    response = client.post("/invoke", json=payload)

    assert response.status_code == 422


def test_rejects_missing_input(client, payload):
    del payload["input"]

    response = client.post("/invoke", json=payload)

    assert response.status_code == 422
