# Design

## Context

See `proposal.md` for motivation. The repository currently contains only documentation, so this change introduces the first executable component. The binding constraints come from `docs/agents/auditor-agente.md` and `docs/overview/project_general_overview.md`: JSON v1.0 envelopes, seven named checks, verdict/score/`return_to`, signed approval, and traceability. There is no existing code, database, or deployment convention to extend.

## Goals / Non-Goals

**Goals:**

- Provide a self-contained, deterministic auditable service that runs without external AI providers.
- Keep the public contract exactly aligned with the documented JSON v1.0 envelopes, including Spanish field names (`veredicto`, `checks`, `hallazgos`, `return_to`).
- Make check outcomes testable and reproducible so the orchestrator can rely on stable behavior.
- Keep signing and trace persistence pluggable through environment variables.

**Non-Goals:**

- Generating or editing content; AG-AUD only evaluates.
- Semantic language-model scoring, embeddings, or network calls. The spelling/coherence checks are deterministic heuristics; they can be replaced later without changing the contract.
- Orchestrator retry counting, WhatsApp integration, Instagram publishing, or a shared database.
- Authentication between agents. The service assumes an internal network boundary.

## Decisions

### Python 3.11 + FastAPI + Pydantic v2

FastAPI provides request validation, OpenAPI documentation, and ASGI deployment with minimal code, while Pydantic models make the JSON v1.0 contract explicit. Alternatives: Node/Express (would require duplicating schema validation) and Flask (weaker typed validation). Python is also the natural fit for later LLM integration mentioned in the docs.

### Deterministic rule-based checks instead of an LLM

The seven checks are implemented with transparent rules: delimiter/spacing heuristics, token overlap, numeric-claim extraction, prohibited-term matching, absolute-claim detection, and platform limits. This makes verdicts reproducible, testable, offline, and free of cost, and it enforces the anti-hallucination requirement without trusting another model. A future LLM-based linguistic scorer can be introduced behind the same `checks` interface, but is explicitly out of scope.

### Check weights and severity

Each check has a fixed penalty: spelling 10, coherence 16, veracity 24, strategy 16, brand 12, risk 12, limits 10 (total 100). Score is `100 - Σ penalties`. `veracidad`, `riesgo`, and `limites` are severe: any failure forces `RECHAZADO` even if the score reaches the threshold. The weights are chosen so that a coherence or strategy failure (16 points) drops the default score below 85 and rejects the content, while a minor spelling (10) or brand (12) issue leaves the score at or above the default threshold. This satisfies the documented rule that false claims and grave incoherence reject content, while keeping `AUDITOR_APPROVAL_THRESHOLD` (default 85) meaningful for softer issues.

### Routing priority

When several checks fail, `return_to` follows root-cause priority: `AG-EST` (strategy) → `AG-BOR` (visual coherence) → `AG-COP` (everything else). Routing the earliest upstream failure prevents the orchestrator from asking a copywriter to fix a wrong brief. `return_to` is `null` on approval.

### Content hash and signature

`content_hash = SHA-256(asset_url + "\n" + caption + "\n" + ",".join(hashtags))`, lowercase hex. Hashtag order is preserved as supplied, so the publisher can recompute it exactly after transport. `content_signature = HMAC-SHA256(AUDITOR_SIGNING_KEY, content_hash_hex)`. The signing key comes from the environment; for development only, a fixed fallback key is used and a warning is emitted in the response so production misconfiguration is visible without blocking local runs. `approval_id` is `APP-<UTC date YYYYMMDD>-<8 hex>`.

### Trace persistence as append-only JSONL

The shared trace table described in the docs is represented as one JSON object per line in `AUDITOR_TRACE_PATH` (default `data/audit_trace.jsonl`). JSONL is append-only, human-readable, trivially ingestible into a database later, and needs no infrastructure. A process-level lock serializes writes. Trace write failures propagate as HTTP 500 because losing the audit trail violates the traceability requirement.

### Input flexibility for the context pack

The docs show `promociones` as strings, but other teams may send objects with IDs. The model accepts promotion entries as strings or objects and stringifies them for matching; `facts` accepts `{id, ...}` objects or strings. Unknown extra fields are allowed so the service remains forward-compatible with evolving business profiles.

## Risks / Trade-offs

- [Heuristic checks can miss real spelling or semantic errors] → Keep results evidence-based, document limitations, and leave the check interface open to an LLM scorer later.
- [Token-overlap coherence produces false passes for short captions] → Require at least one meaningful word and report the matched token in the evidence for auditability.
- [Fixed development signing key could leak into production] → Emit a `warnings` entry when the fallback key is active and document `AUDITOR_SIGNING_KEY` as required in deployment.
- [JSONL trace grows unbounded] → Accept for this scope; rotation/ingestion is an operational concern outside the change.
- [Numeric matching is literal (e.g. `20%` vs `20 %`)] → Normalize spacing before comparison and cover it with tests.

## Migration Plan

1. Install dependencies and run the service with `uvicorn app.main:app`.
2. Configure `AUDITOR_SIGNING_KEY`, `AUDITOR_TRACE_PATH`, and `AUDITOR_APPROVAL_THRESHOLD` in the deployment environment.
3. Point AG-ORQ at `POST /invoke` for step 5.
4. Rollback is deleting the new service and its dependencies; no existing behavior is modified.
