"""HTTP contract tests beyond the baseline happy-path suite."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest


def post(client, payload):
    return client.post("/invoke", json=payload)


def test_package_exposes_the_service_version():
    import app

    assert app.__version__ == "1.0.0"
    assert app.__all__ == ["__version__"]


def assert_validation_error(response):
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert detail


def test_health_returns_json_with_the_expected_headers(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok", "agent_id": "AG-AUD"}


def test_health_does_not_write_a_trace(client, trace_path):
    client.get("/health")

    assert not Path(trace_path).exists()


def test_openapi_documents_the_invoke_and_health_routes(client):
    specification = client.get("/openapi.json").json()

    assert specification["info"]["title"] == "AG-AUD Auditor API"
    assert specification["info"]["version"] == "1.0.0"
    assert "/health" in specification["paths"]
    assert "/invoke" in specification["paths"]
    assert "post" in specification["paths"]["/invoke"]


def test_unknown_route_returns_404(client):
    assert client.get("/does-not-exist").status_code == 404


def test_invoke_only_accepts_post(client):
    response = client.get("/invoke")

    assert response.status_code == 405


@pytest.mark.parametrize(
    "field",
    ["contract_version", "execution_id", "step", "iteration", "agent_id", "input"],
)
def test_missing_required_envelope_fields_return_422(client, payload, field):
    del payload[field]

    assert_validation_error(post(client, payload))


def test_missing_nested_input_sections_return_422(client, payload):
    for section in ("brief", "borrador", "copy", "context_pack"):
        broken = json.loads(json.dumps(payload))
        del broken["input"][section]

        assert_validation_error(post(client, broken))


def test_missing_caption_returns_422(client, payload):
    del payload["input"]["copy"]["caption"]

    assert_validation_error(post(client, payload))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("step",), 0),
        (("step",), -1),
        (("iteration",), 0),
        (("iteration",), -2),
        (("contract_version",), "1.1"),
        (("contract_version",), None),
        (("agent_id",), "AG-ORQ"),
        (("execution_id",), ""),
        (("execution_id",), "   "),
        (("sent_at",), "not-a-date"),
        (("input", "borrador", "asset_url"), ""),
        (("input", "borrador", "asset_url"), None),
        (("input", "copy", "hashtags"), "no-list"),
        (("input", "copy", "hashtags"), [1, 2]),
        (("input", "copy", "facts_used"), "FACT-1"),
        (("input", "context_pack", "empresa"), 123),
    ],
)
def test_invalid_field_values_return_422(client, payload, path, value):
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert_validation_error(post(client, payload))


def test_non_json_body_returns_422(client):
    response = client.post(
        "/invoke", content="not-json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422


def test_json_root_that_is_not_an_object_returns_422(client):
    assert client.post("/invoke", json=["not", "an", "object"]).status_code == 422


def test_extra_root_fields_are_accepted_and_echoed_in_the_trace(client, payload, trace_path):
    payload["extra_root"] = "kept"
    payload["input"]["extra_section"] = {"kept": True}

    response = post(client, payload)

    assert response.status_code == 200
    body = response.json()
    assert "extra_root" not in body
    trace = json.loads(Path(trace_path).read_text(encoding="utf-8").splitlines()[0])
    assert trace["input_payload"]["extra_root"] == "kept"
    assert trace["input_payload"]["input"]["extra_section"] == {"kept": True}


def test_documented_string_context_shapes_are_accepted(client, payload):
    payload["input"]["context_pack"] = {
        "empresa": "RetailVenture C.A.",
        "promociones": ["20% OFF en ropa deportiva (Sab-Dom)"],
        "facts": ["FACT-012 Envío gratis"],
    }

    response = post(client, payload)

    assert response.status_code == 200
    assert response.json()["output"]["veredicto"] == "APROBADO"


def test_forward_compatible_rule_shapes_are_accepted(client, payload):
    for rules in (
        {"palabras_prohibidas": ["barato"]},
        [{"termino": "barato"}],
        "tono cercano",
        None,
    ):
        candidate = json.loads(json.dumps(payload))
        candidate["input"]["context_pack"]["reglas_marca"] = rules

        assert post(client, candidate).status_code == 200


def test_rejected_invocation_returns_the_rejected_status_and_no_approval(client, payload):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."

    response = post(client, payload)
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "rejected"
    assert body["output"]["veredicto"] == "RECHAZADO"
    assert body["output"]["return_to"] == "AG-EST"
    assert body["output"]["approval_id"] is None
    assert body["error"] is None


def test_threshold_environment_variable_changes_the_api_verdict(
    client, payload, monkeypatch
):
    payload["input"]["copy"]["caption"] = "Visita  la tienda con 20% OFF."
    monkeypatch.setenv("AUDITOR_APPROVAL_THRESHOLD", "91")

    body = post(client, payload).json()

    assert body["output"]["score"] == 90
    assert body["output"]["veredicto"] == "RECHAZADO"


def test_ai_enabled_without_credential_still_returns_200_with_a_warning(
    client, payload, monkeypatch
):
    monkeypatch.setenv("AUDITOR_AI_ENABLED", "true")
    monkeypatch.setenv("AI_KEY", "")

    response = post(client, payload)

    assert response.status_code == 200
    body = response.json()
    assert body["output"]["veredicto"] == "APROBADO"
    assert len(body["warnings"]) == 1
    assert "AI_KEY" in body["warnings"][0]


def test_response_matches_the_documented_output_field_types(client, payload):
    body = post(client, payload).json()
    output = body["output"]

    assert isinstance(body["execution_id"], str)
    assert isinstance(body["data_sources"], list)
    assert isinstance(body["warnings"], list)
    assert body["error"] is None
    assert isinstance(output["score"], int)
    assert output["score"] == 100
    assert isinstance(output["checks"], list)
    assert isinstance(output["hallazgos"], list)
    assert output["return_to"] is None
    assert isinstance(output["approval_id"], str)
    assert isinstance(output["content_hash"], str)
    assert isinstance(output["content_signature"], str)


def test_trace_written_by_the_api_contains_the_processed_payload(client, payload, trace_path):
    body = post(client, payload).json()

    lines = Path(trace_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    trace = json.loads(lines[0])
    assert trace["execution_id"] == payload["execution_id"]
    assert trace["status"] == body["status"]
    assert trace["output_payload"] == body["output"]
    assert trace["data_sources"] == body["data_sources"]
    assert trace["input_payload"]["input"]["copy"]["caption"] == (
        payload["input"]["copy"]["caption"]
    )
    assert datetime.fromisoformat(trace["started_at"]) == datetime.fromisoformat(
        body["started_at"]
    )
    assert datetime.fromisoformat(trace["finished_at"]) == datetime.fromisoformat(
        body["finished_at"]
    )


def test_each_api_invocation_appends_one_trace_line(client, payload, trace_path):
    for index in range(3):
        candidate = json.loads(json.dumps(payload))
        candidate["execution_id"] = f"EXE-{index}"
        assert post(client, candidate).status_code == 200

    lines = Path(trace_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["execution_id"] for line in lines] == [
        "EXE-0", "EXE-1", "EXE-2"
    ]


def test_validation_errors_do_not_write_a_trace(client, payload, trace_path):
    payload["contract_version"] = "9.9"

    assert post(client, payload).status_code == 422
    assert not Path(trace_path).exists()
