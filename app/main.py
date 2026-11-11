"""FastAPI entry point for the AG-AUD auditor service."""

from __future__ import annotations

from fastapi import FastAPI

from .config import load_settings
from .models import AuditRequest, AuditResponse
from .service import run_audit
from .trace import append_trace

app = FastAPI(
    title="AG-AUD Auditor API",
    version="1.0.0",
    description=(
        "Agente Auditor (AG-AUD) del Retail Content Factory. "
        "Evalúa borrador, copy, brief y contexto de negocio bajo el contrato JSON v1.0."
    ),
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent_id": "AG-AUD"}


@app.post("/invoke", response_model=AuditResponse)
def invoke(request: AuditRequest) -> AuditResponse:
    settings = load_settings()
    response = run_audit(request, settings)

    append_trace(
        settings.trace_path,
        {
            "execution_id": response.execution_id,
            "iteration": request.iteration,
            "step": request.step,
            "agent_id": request.agent_id,
            "input_payload": request.model_dump(mode="json", by_alias=True),
            "output_payload": response.output.model_dump(mode="json"),
            "data_sources": [source.model_dump() for source in response.data_sources],
            "status": response.status,
            "started_at": response.started_at.isoformat(),
            "finished_at": response.finished_at.isoformat(),
        },
    )
    return response
