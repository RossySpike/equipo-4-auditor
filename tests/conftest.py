"""Shared fixtures and a documented-contract sample payload."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.main import app

BASE_PAYLOAD = {
    "contract_version": "1.0",
    "execution_id": "EXE-0007",
    "step": 5,
    "iteration": 1,
    "agent_id": "AG-AUD",
    "business_profile_id": "BP-001",
    "user_request": (
        "Quiero una publicacion para promocionar la liquidacion de ropa "
        "deportiva este fin de semana"
    ),
    "input": {
        "brief": {
            "idea_id": "IDEA-01",
            "objetivo": "Trafico a tienda",
            "audiencia": "Adultos 25-40",
        },
        "borrador": {
            "asset_url": "https://cdn.example.com/posts/liquidacion.jpg",
            "alt_text": "Modelo en tienda deportiva con prendas en oferta",
        },
        "copy": {
            "caption": (
                "¡Este fin de semana tu rutina se renueva! 20% OFF en ropa "
                "deportiva. Visita la tienda."
            ),
            "hashtags": ["#Deporte", "#Oferta", "#FinDeSemana"],
            "facts_used": ["FACT-012"],
        },
        "context_pack": {
            "empresa": "RetailVenture C.A.",
            "promociones": [
                {"id": "PROMO-003", "texto": "20% OFF en ropa deportiva (Sab-Dom)"}
            ],
            "facts": [
                {"id": "FACT-012", "texto": "20% OFF en ropa deportiva (Sab-Dom)"}
            ],
            "reglas_marca": {"palabras_prohibidas": ["barato"]},
            "tono": "cercano",
        },
    },
    "sent_at": "2026-11-11T14:32:10-04:00",
}


@pytest.fixture(autouse=True)
def _disable_external_ai(monkeypatch):
    """Keep the suite hermetic: never call Gemini, even if `.env` has a key."""

    monkeypatch.setenv("AUDITOR_AI_ENABLED", "false")
    monkeypatch.delenv("AI_KEY", raising=False)
    monkeypatch.delenv("AUDITOR_AI_KEY", raising=False)


@pytest.fixture
def payload() -> dict:
    return copy.deepcopy(BASE_PAYLOAD)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("AUDITOR_TRACE_PATH", str(tmp_path / "audit_trace.jsonl"))
    monkeypatch.setenv("AUDITOR_SIGNING_KEY", "test-signing-key")
    monkeypatch.delenv("AUDITOR_APPROVAL_THRESHOLD", raising=False)
    return TestClient(app)


@pytest.fixture
def trace_path(tmp_path) -> str:
    return str(tmp_path / "audit_trace.jsonl")
