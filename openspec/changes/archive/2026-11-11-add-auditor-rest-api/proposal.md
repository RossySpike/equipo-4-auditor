# Proposal

## Why

The project documents the AG-AUD auditor's responsibilities and the JSON v1.0 inter-agent contract, but there is no executable service the orchestrator (AG-ORQ) can call at step 5. Without a REST endpoint, quality control, anti-hallucination verification, and approval signing cannot happen, so no content can be legitimately published.

## What Changes

- Add a Python/FastAPI service that exposes the AG-AUD auditor over synchronous HTTP/JSON using contract version `1.0`.
- Add `POST /invoke` implementing the standard request/response envelope (`contract_version`, `execution_id`, `step`, `iteration`, `agent_id`, `business_profile_id`, `user_request`, `input`, `sent_at`).
- Implement the seven audit checks over `brief`, `borrador`, `copy`, and `context_pack`: spelling/grammar/clarity, text-image-idea coherence, commercial fact veracity, strategic/audience alignment, brand voice and tone, unsupported-claim risk, and Instagram technical limits.
- Implement deterministic scoring (0-100), configurable approval threshold, verdict resolution (`APROBADO`/`RECHAZADO`), `hallazgos[]`, and `return_to` routing to `AG-EST`, `AG-BOR`, or `AG-COP`.
- Generate `approval_id` and a SHA-256 `content_hash` over `asset_url` + `caption` + `hashtags`, plus an HMAC signature so AG-PUB can verify integrity.
- Persist one trace record per invocation (JSONL) containing the envelope, output, data sources, status, and timestamps.
- Add `GET /health` for liveness and dependency-free operation.
- Add automated tests covering the contract, each check, verdict/routing, hashing, and trace persistence.

## Capabilities

### New Capabilities

- `auditor-api`: HTTP/JSON invocation contract, audit checks, verdict/scoring/routing, approval signing, and trace persistence for AG-AUD.

### Modified Capabilities

<!-- None: no existing capabilities exist in this project. -->

## Impact

- New application code under `app/` and tests under `tests/`.
- New runtime dependencies: `fastapi`, `uvicorn`, `pydantic` (v2), and `pytest`/`httpx` for tests.
- New environment variables: `AUDITOR_SIGNING_KEY` (required in production, deterministic development default) and `AUDITOR_APPROVAL_THRESHOLD` (default `85`).
- Runtime writes to `data/audit_trace.jsonl` (path configurable via `AUDITOR_TRACE_PATH`).
- No changes to existing documentation contracts; the implementation conforms to `docs/agents/auditor-agente.md` and `docs/overview/project_general_overview.md`.
