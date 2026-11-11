# Tasks

## 1. Project scaffold

- [x] 1.1 Create `requirements.txt` with FastAPI, Uvicorn, Pydantic v2, pytest, and httpx.
- [x] 1.2 Create the `app/` package with `__init__.py`, `config.py` (environment settings), and a `pytest.ini`/test configuration.

## 2. Contract models

- [x] 2.1 Implement Pydantic models for the JSON v1.0 request envelope, nested `brief`, `borrador`, `copy`, and flexible `context_pack`.
- [x] 2.2 Implement response models for `checks`, `hallazgos`, `data_sources`, and the output/response envelopes.
- [x] 2.3 Enforce `contract_version == "1.0"` and `agent_id == "AG-AUD"` with HTTP 422 on violation.

## 3. Audit checks

- [x] 3.1 Implement the spelling/grammar/clarity check with delimiter, placeholder, and spacing heuristics.
- [x] 3.2 Implement the text-image-idea coherence check using meaningful-token overlap.
- [x] 3.3 Implement the commercial veracity check for percentages, prices, and `facts_used` identifiers against `context_pack`.
- [x] 3.4 Implement the strategic/audience alignment check based on CTA detection and objective overlap.
- [x] 3.5 Implement the brand voice/tone check for prohibited terms and upper-case abuse.
- [x] 3.6 Implement the unsupported-claim risk check for absolute or deceptive promises.
- [x] 3.7 Implement the Instagram limits check for caption length, hashtag count/format, and image extension.

## 4. Verdict, routing, and signing

- [x] 4.1 Implement weighted scoring, severe-check handling, and configurable approval threshold.
- [x] 4.2 Implement `return_to` routing priority (`AG-EST` → `AG-BOR` → `AG-COP`) and `null` on approval.
- [x] 4.3 Implement `approval_id` generation, canonical SHA-256 `content_hash`, and HMAC-SHA256 `content_signature`.
- [x] 4.4 Collect `data_sources` from matched promotions/facts and emit the development-key warning.

## 5. Service and traceability

- [x] 5.1 Implement append-only JSONL trace persistence with a process lock and configurable path.
- [x] 5.2 Implement `POST /invoke` wiring validation, checks, scoring, signing, and trace in one request flow.
- [x] 5.3 Implement `GET /health` returning `status` and `agent_id`.

## 6. Tests

- [x] 6.1 Add API tests for `/health`, the valid invocation contract, unsupported version, and wrong agent id.
- [x] 6.2 Add tests for each of the seven checks, covering pass and fail scenarios from the spec.
- [x] 6.3 Add tests for approval, rejection, threshold behavior, severe failures, and routing.
- [x] 6.4 Add tests for hash determinism, signature verification, null approval fields on rejection, and trace line contents.

## 7. Documentation and verification

- [x] 7.1 Document installation, environment variables, endpoints, and an example invocation in `README.md`.
- [x] 7.2 Run the full test suite and fix any failures.
- [x] 7.3 Run `openspec validate add-auditor-rest-api` and resolve any spec issues.
