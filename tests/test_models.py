"""Pydantic contract models: validation, aliases, defaults, and bounds."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    AuditError,
    AuditInput,
    AuditOutput,
    AuditRequest,
    AuditResponse,
    Brief,
    CheckResult,
    ContextPack,
    CopyPayload,
    DataSource,
    Hallazgo,
)


def minimal_request(**overrides) -> dict:
    payload = {
        "contract_version": "1.0",
        "execution_id": "EXE-MODEL-1",
        "step": 5,
        "iteration": 1,
        "agent_id": "AG-AUD",
        "input": {
            "brief": {"objetivo": "Trafico a tienda"},
            "borrador": {"asset_url": "https://cdn.example.com/a.jpg"},
            "copy": {"caption": "Visita la tienda"},
            "context_pack": {},
        },
    }
    payload.update(overrides)
    return payload


def test_request_accepts_the_documented_envelope_and_copy_alias():
    request = AuditRequest.model_validate(minimal_request())

    assert request.contract_version == "1.0"
    assert request.execution_id == "EXE-MODEL-1"
    assert request.step == 5
    assert request.iteration == 1
    assert request.agent_id == "AG-AUD"
    assert request.input.copy_payload.caption == "Visita la tienda"
    assert request.sent_at is None


def test_request_serializes_the_copy_field_with_its_alias():
    request = AuditRequest.model_validate(minimal_request())

    dumped = request.model_dump(mode="json", by_alias=True)

    assert "copy" in dumped["input"]
    assert "copy_payload" not in dumped["input"]
    assert dumped["input"]["copy"]["caption"] == "Visita la tienda"


def test_sent_at_is_parsed_as_a_datetime():
    request = AuditRequest.model_validate(
        minimal_request(sent_at="2026-11-11T14:32:10-04:00")
    )

    assert isinstance(request.sent_at, datetime)
    assert request.sent_at.utcoffset() is not None


@pytest.mark.parametrize("bad_version", ["2.0", "1", "v1.0", "", None])
def test_unsupported_contract_versions_are_rejected(bad_version):
    with pytest.raises(ValidationError):
        AuditRequest.model_validate(minimal_request(contract_version=bad_version))


@pytest.mark.parametrize("bad_agent", ["AG-ORQ", "AG-COP", "AG-AUD ", "ag-aud", ""])
def test_wrong_agent_identifiers_are_rejected(bad_agent):
    with pytest.raises(ValidationError):
        AuditRequest.model_validate(minimal_request(agent_id=bad_agent))


@pytest.mark.parametrize("bad_execution_id", ["", "   ", "\t", "\n"])
def test_blank_execution_ids_are_rejected(bad_execution_id):
    with pytest.raises(ValidationError):
        AuditRequest.model_validate(minimal_request(execution_id=bad_execution_id))


def test_execution_id_is_kept_verbatim_when_it_has_content():
    request = AuditRequest.model_validate(minimal_request(execution_id=" EXE-1 "))

    assert request.execution_id == " EXE-1 "


@pytest.mark.parametrize("field", ["contract_version", "execution_id", "step",
                                   "iteration", "agent_id", "input"])
def test_required_envelope_fields_are_enforced(field):
    payload = minimal_request()
    del payload[field]

    with pytest.raises(ValidationError):
        AuditRequest.model_validate(payload)


@pytest.mark.parametrize(("step", "iteration"), [(0, 1), (-1, 1), (1, 0), (1, -3)])
def test_step_and_iteration_must_be_positive(step, iteration):
    with pytest.raises(ValidationError):
        AuditRequest.model_validate(minimal_request(step=step, iteration=iteration))


def test_extra_fields_are_allowed_and_preserved_at_every_level():
    payload = minimal_request(extra_root="root-extra")
    payload["input"]["extra_input"] = "input-extra"
    payload["input"]["brief"]["extra_brief"] = "brief-extra"
    payload["input"]["borrador"]["extra_borrador"] = "borrador-extra"
    payload["input"]["copy"]["extra_copy"] = "copy-extra"
    payload["input"]["context_pack"]["extra_context"] = "context-extra"

    request = AuditRequest.model_validate(payload)
    dumped = request.model_dump(mode="json", by_alias=True)

    assert dumped["extra_root"] == "root-extra"
    assert dumped["input"]["extra_input"] == "input-extra"
    assert dumped["input"]["brief"]["extra_brief"] == "brief-extra"
    assert dumped["input"]["borrador"]["extra_borrador"] == "borrador-extra"
    assert dumped["input"]["copy"]["extra_copy"] == "copy-extra"
    assert dumped["input"]["context_pack"]["extra_context"] == "context-extra"


@pytest.mark.parametrize("asset_url", ["", None, 123])
def test_asset_url_must_be_a_non_empty_string(asset_url):
    payload = minimal_request()
    payload["input"]["borrador"]["asset_url"] = asset_url

    with pytest.raises(ValidationError):
        AuditRequest.model_validate(payload)


def test_borrador_optional_fields_default_correctly():
    borrador = AuditRequest.model_validate(minimal_request()).input.borrador

    assert borrador.alt_text == ""
    assert borrador.concepto_visual is None


def test_copy_defaults_and_optional_fields():
    payload = minimal_request()
    payload["input"]["copy"] = {"caption": "Compra ya", "hook": "h", "cta": "Compra"}

    copy = AuditRequest.model_validate(payload).input.copy_payload

    assert copy.hashtags == []
    assert copy.facts_used == []
    assert copy.hook == "h"
    assert copy.cta == "Compra"


@pytest.mark.parametrize("copy_value", [None, "text", []])
def test_copy_payload_rejects_invalid_shapes(copy_value):
    payload = minimal_request()
    payload["input"]["copy"] = copy_value

    with pytest.raises(ValidationError):
        AuditRequest.model_validate(payload)


@pytest.mark.parametrize("hashtags", ["#uno", [1, 2], {"a": 1}, None])
def test_hashtags_must_be_a_list_of_strings(hashtags):
    payload = minimal_request()
    payload["input"]["copy"]["hashtags"] = hashtags

    with pytest.raises(ValidationError):
        AuditRequest.model_validate(payload)


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"empresa": None, "promociones": [], "facts": []},
        {"promociones": ["20% OFF", {"id": "P-1", "texto": "x"}]},
        {"facts": ["FACT-001 texto", {"id": "FACT-002", "texto": "x"}]},
        {"reglas_marca": {"palabras_prohibidas": ["barato"]}},
        {"reglas_marca": [{"termino": "barato"}]},
        {"reglas_marca": "tono cercano"},
        {"reglas_marca": None},
    ],
)
def test_context_pack_accepts_documented_and_forward_compatible_shapes(context):
    context_pack = ContextPack.model_validate(context)

    assert isinstance(context_pack.promociones, list)
    assert isinstance(context_pack.facts, list)


def test_brief_defaults_are_empty_strings():
    brief = Brief.model_validate({})

    assert brief.idea_id is None
    assert brief.objetivo == ""
    assert brief.audiencia == ""


def test_copy_payload_requires_caption():
    with pytest.raises(ValidationError):
        CopyPayload.model_validate({})


def test_audit_input_requires_all_four_sections():
    with pytest.raises(ValidationError):
        AuditInput.model_validate({"brief": {}, "borrador": {"asset_url": "x"}})


def test_check_result_only_accepts_documented_results():
    assert CheckResult(criterio="c", resultado="PASSED", evidencia="e").resultado == "PASSED"

    with pytest.raises(ValidationError):
        CheckResult(criterio="c", resultado="OK", evidencia="e")


def test_hallazgo_severities():
    for severity in ("alta", "media", "baja"):
        assert Hallazgo(criterio="c", detalle="d", severidad=severity).severidad == severity

    with pytest.raises(ValidationError):
        Hallazgo(criterio="c", detalle="d", severidad="critica")


@pytest.mark.parametrize("score", [-1, 101, 1000])
def test_output_score_must_be_between_zero_and_one_hundred(score):
    with pytest.raises(ValidationError):
        AuditOutput(
            veredicto="APROBADO",
            score=score,
            checks=[],
            hallazgos=[],
            return_to=None,
            approval_id=None,
            content_hash=None,
            content_signature=None,
        )


def test_output_accepts_boundary_scores_and_every_return_route():
    for score in (0, 100):
        output = AuditOutput(
            veredicto="RECHAZADO",
            score=score,
            checks=[],
            hallazgos=[],
            return_to="AG-COP",
            approval_id=None,
            content_hash=None,
            content_signature=None,
        )
        assert output.score == score

    for route in ("AG-EST", "AG-BOR", "AG-COP", None):
        output = AuditOutput(
            veredicto="RECHAZADO",
            score=50,
            checks=[],
            hallazgos=[],
            return_to=route,
            approval_id=None,
            content_hash=None,
            content_signature=None,
        )
        assert output.return_to == route


def test_output_rejects_invalid_verdict_and_route():
    with pytest.raises(ValidationError):
        AuditOutput(
            veredicto="TAL VEZ",
            score=50,
            checks=[],
            hallazgos=[],
            return_to=None,
            approval_id=None,
            content_hash=None,
            content_signature=None,
        )

    with pytest.raises(ValidationError):
        AuditOutput(
            veredicto="RECHAZADO",
            score=50,
            checks=[],
            hallazgos=[],
            return_to="AG-PUB",
            approval_id=None,
            content_hash=None,
            content_signature=None,
        )


def test_data_source_model():
    source = DataSource(source="perfil_negocio.facts", ref="FACT-001")

    assert source.model_dump() == {"source": "perfil_negocio.facts", "ref": "FACT-001"}


def test_response_defaults_and_envelope_fields():
    response = AuditResponse(
        execution_id="EXE-1",
        status="completed",
        output=AuditOutput(
            veredicto="APROBADO",
            score=100,
            checks=[],
            hallazgos=[],
            return_to=None,
            approval_id="APP-1",
            content_hash="a" * 64,
            content_signature="b" * 64,
        ),
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )

    dumped = response.model_dump(mode="json")

    assert dumped["contract_version"] == "1.0"
    assert dumped["agent_id"] == "AG-AUD"
    assert dumped["data_sources"] == []
    assert dumped["warnings"] == []
    assert dumped["error"] is None


def test_response_rejects_invalid_status():
    with pytest.raises(ValidationError):
        AuditResponse(
            execution_id="EXE-1",
            status="needs_info",
            output=AuditOutput(
                veredicto="RECHAZADO",
                score=0,
                checks=[],
                hallazgos=[],
                return_to="AG-COP",
                approval_id=None,
                content_hash=None,
                content_signature=None,
            ),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )


def test_audit_error_model():
    error = AuditError(code="AI_TIMEOUT", message="proveedor no disponible")

    assert error.model_dump() == {"code": "AI_TIMEOUT", "message": "proveedor no disponible"}
