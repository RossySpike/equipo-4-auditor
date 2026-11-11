"""Hash, signature, trace, and configuration-warning tests."""

from __future__ import annotations

import json

from app.signing import verify_content_signature


def invoke(client, payload):
    response = client.post("/invoke", json=payload)
    assert response.status_code == 200
    return response.json()


def test_content_hash_is_deterministic(client, payload):
    first = invoke(client, payload)["output"]["content_hash"]
    second = invoke(client, payload)["output"]["content_hash"]

    assert first == second


def test_content_signature_is_verifiable_with_the_configured_key(client, payload):
    output = invoke(client, payload)["output"]

    assert verify_content_signature(
        output["content_hash"], output["content_signature"], "test-signing-key"
    )


def test_signature_is_not_valid_with_another_key(client, payload):
    output = invoke(client, payload)["output"]

    assert not verify_content_signature(
        output["content_hash"], output["content_signature"], "another-key"
    )


def test_trace_line_contains_the_documented_fields(client, payload, trace_path):
    body = invoke(client, payload)

    with open(trace_path, encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]

    assert len(lines) == 1
    trace = lines[0]
    assert set(trace) == {
        "execution_id",
        "iteration",
        "step",
        "agent_id",
        "input_payload",
        "output_payload",
        "data_sources",
        "status",
        "started_at",
        "finished_at",
    }
    assert trace["execution_id"] == payload["execution_id"]
    assert trace["iteration"] == payload["iteration"]
    assert trace["step"] == payload["step"]
    assert trace["agent_id"] == "AG-AUD"
    assert trace["status"] == body["status"]
    assert trace["output_payload"]["veredicto"] == "APROBADO"
    assert trace["input_payload"]["input"]["copy"]["caption"] == payload["input"]["copy"]["caption"]


def test_trace_is_appended_for_rejected_content(client, payload, trace_path):
    payload["input"]["copy"]["caption"] = "La nueva colección ya está aquí."
    invoke(client, payload)
    invoke(client, payload)

    with open(trace_path, encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]

    assert len(lines) == 2
    assert all(line["status"] == "rejected" for line in lines)


def test_warns_when_signing_key_is_not_configured(client, payload, monkeypatch):
    monkeypatch.delenv("AUDITOR_SIGNING_KEY", raising=False)

    body = invoke(client, payload)

    assert body["warnings"]
    assert "AUDITOR_SIGNING_KEY" in body["warnings"][0]
