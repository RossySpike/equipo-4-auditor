"""Audit orchestration: checks -> score -> verdict -> signing."""

from __future__ import annotations

from datetime import datetime, timezone

from .checks import CheckOutcome, run_checks
from .config import Settings
from .models import (
    AuditOutput,
    AuditRequest,
    AuditResponse,
    CheckResult,
    DataSource,
    Hallazgo,
)
from .signing import (
    compute_content_hash,
    generate_approval_id,
    sign_content_hash,
)

ROUTING_PRIORITY = ("AG-EST", "AG-BOR", "AG-COP")


def _select_return_to(outcomes: list[CheckOutcome]) -> str:
    failed = [outcome for outcome in outcomes if not outcome.passed]
    for route in ROUTING_PRIORITY:
        if any(outcome.route == route for outcome in failed):
            return route
    return "AG-COP"


def _dedupe_sources(outcomes: list[CheckOutcome]) -> list[DataSource]:
    seen: set[tuple[str, str]] = set()
    sources: list[DataSource] = []
    for outcome in outcomes:
        for source in outcome.data_sources:
            key = (source.source, source.ref)
            if key not in seen:
                seen.add(key)
                sources.append(source)
    return sources


def run_audit(request: AuditRequest, settings: Settings) -> AuditResponse:
    """Evaluate one invocation and build the contract response."""

    started_at = datetime.now(timezone.utc)
    outcomes = run_checks(request)

    penalty = sum(outcome.weight for outcome in outcomes if not outcome.passed)
    score = max(0, 100 - penalty)
    severe_failure = any(not outcome.passed and outcome.severe for outcome in outcomes)
    approved = score >= settings.approval_threshold and not severe_failure

    checks = [
        CheckResult(
            criterio=outcome.criterio,
            resultado="PASSED" if outcome.passed else "FAILED",
            evidencia=outcome.evidencia,
        )
        for outcome in outcomes
    ]
    hallazgos = [
        Hallazgo(
            criterio=outcome.criterio,
            detalle=outcome.hallazgo or outcome.evidencia,
            severidad="alta" if outcome.severe else "media",
        )
        for outcome in outcomes
        if not outcome.passed
    ]

    warnings: list[str] = []
    if settings.using_development_key:
        warnings.append(
            "AUDITOR_SIGNING_KEY no está configurada; se usa una clave de desarrollo."
        )

    copy_payload = request.input.copy_payload
    if approved:
        content_hash = compute_content_hash(
            request.input.borrador.asset_url,
            copy_payload.caption,
            copy_payload.hashtags,
        )
        output = AuditOutput(
            veredicto="APROBADO",
            score=score,
            checks=checks,
            hallazgos=hallazgos,
            return_to=None,
            approval_id=generate_approval_id(started_at),
            content_hash=content_hash,
            content_signature=sign_content_hash(content_hash, settings.signing_key),
        )
        status = "completed"
    else:
        output = AuditOutput(
            veredicto="RECHAZADO",
            score=score,
            checks=checks,
            hallazgos=hallazgos,
            return_to=_select_return_to(outcomes),
            approval_id=None,
            content_hash=None,
            content_signature=None,
        )
        status = "rejected"

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
