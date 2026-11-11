"""Pydantic models for the AG-AUD JSON v1.0 contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Brief(BaseModel):
    """Strategic brief produced by AG-EST."""

    model_config = ConfigDict(extra="allow")

    idea_id: str | None = None
    objetivo: str = ""
    audiencia: str = ""


class Borrador(BaseModel):
    """Visual draft produced by AG-BOR."""

    model_config = ConfigDict(extra="allow")

    asset_url: str = Field(min_length=1)
    alt_text: str = ""
    concepto_visual: str | None = None


class CopyPayload(BaseModel):
    """Copy produced by AG-COP."""

    model_config = ConfigDict(extra="allow")

    caption: str
    hashtags: list[str] = Field(default_factory=list)
    facts_used: list[str] = Field(default_factory=list)
    hook: str | None = None
    cta: str | None = None


class ContextPack(BaseModel):
    """Business context supplied by AG-CTX.

    Promotions and facts accept strings (as shown in the project docs) or
    objects (as other teams may send) so the contract stays forward-compatible.
    """

    model_config = ConfigDict(extra="allow")

    empresa: str | None = None
    promociones: list[str | dict[str, Any]] = Field(default_factory=list)
    facts: list[str | dict[str, Any]] = Field(default_factory=list)
    reglas_marca: dict[str, Any] | list[Any] | str | None = None
    palabras_prohibidas: list[str] = Field(default_factory=list)
    tono: str | None = None


class AuditInput(BaseModel):
    """The `input` object of an audit invocation."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    brief: Brief
    borrador: Borrador
    copy_payload: CopyPayload = Field(alias="copy")
    context_pack: ContextPack


class AuditRequest(BaseModel):
    """JSON v1.0 request envelope for AG-AUD."""

    model_config = ConfigDict(extra="allow")

    contract_version: Literal["1.0"]
    execution_id: str = Field(min_length=1)
    step: int = Field(ge=1)
    iteration: int = Field(ge=1)
    agent_id: Literal["AG-AUD"]
    business_profile_id: str | None = None
    user_request: str | None = None
    input: AuditInput
    sent_at: datetime | None = None

    @field_validator("execution_id")
    @classmethod
    def _execution_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("execution_id must not be blank")
        return value


class CheckResult(BaseModel):
    criterio: str
    resultado: Literal["PASSED", "FAILED"]
    evidencia: str


class Hallazgo(BaseModel):
    criterio: str
    detalle: str
    severidad: Literal["alta", "media", "baja"]


class DataSource(BaseModel):
    source: str
    ref: str


class AuditOutput(BaseModel):
    veredicto: Literal["APROBADO", "RECHAZADO"]
    score: int = Field(ge=0, le=100)
    checks: list[CheckResult]
    hallazgos: list[Hallazgo]
    return_to: Literal["AG-EST", "AG-BOR", "AG-COP"] | None
    approval_id: str | None
    content_hash: str | None
    content_signature: str | None


class AuditError(BaseModel):
    code: str
    message: str


class AuditResponse(BaseModel):
    contract_version: Literal["1.0"] = "1.0"
    execution_id: str
    agent_id: Literal["AG-AUD"] = "AG-AUD"
    status: Literal["completed", "rejected", "failed"]
    output: AuditOutput
    data_sources: list[DataSource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: AuditError | None = None
    started_at: datetime
    finished_at: datetime
