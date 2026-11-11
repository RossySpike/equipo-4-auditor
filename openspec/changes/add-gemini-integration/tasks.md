# Tasks

## 1. Dependencies and settings

- [ ] 1.1 Add `pydantic-settings>=2.2,<3.0` and `google-generativeai>=0.8,<1.0` to `requirements.txt`, install them with `pip install -r requirements.txt`, and verify both imports succeed with `python3 -c "import pydantic_settings, google.generativeai"`.
- [ ] 1.2 Rewrite `app/config.py` as a frozen `BaseSettings` with `env_file=PROJECT_ROOT / ".env"`, `env_prefix="AUDITOR_"`, `AI_KEY` aliased to a `SecretStr`, and `load_settings()` returning `Settings()`; verify via `tests/test_gemini.py` that a temporary `.env` containing a quoted `AI_KEY` is read, the process environment overrides it, and a blank value resolves to `None`.
- [ ] 1.3 Preserve `AUDITOR_SIGNING_KEY`, `AUDITOR_APPROVAL_THRESHOLD` (invalid/out-of-range falls back to `85`), `AUDITOR_TRACE_PATH`, `using_development_key`, and the resolved development key; verify `python3 -m pytest tests/test_signing_trace.py tests/test_verdict.py` passes.
- [ ] 1.4 Add `AUDITOR_AI_ENABLED`, `AUDITOR_AI_MODEL` (default `gemini-2.5-flash`), and `AUDITOR_AI_TIMEOUT_SECONDS` (default `20`) to the settings and verify their defaults and environment overrides in `tests/test_gemini.py`.

## 2. Gemini adapter

- [ ] 2.1 Create `app/gemini.py` with `SEMANTIC_CRITERIA`, `SYSTEM_PROMPT`, `AiCheck`, `AuditAI`, `GeminiAuditor`, `build_gemini_auditor`, `build_prompt`, and `parse_review`; verify the module imports without importing `google.generativeai` at module load.
- [ ] 2.2 Implement the lazy `google.generativeai` instantiation with `genai.configure`, `GenerativeModel(model_name=..., system_instruction=..., generation_config=GenerationConfig(temperature=0, top_p=1, candidate_count=1, max_output_tokens=2048, response_mime_type="application/json"))`, a configure lock, and `request_options={"timeout": ...}`; verify with a monkeypatched fake `google.generativeai` module that the expected arguments are used.
- [ ] 2.3 Implement strict parsing for fenced/prefixed JSON, the five canonical criteria, `PASSED`/`FAILED` results, 600-character evidence/finding limits, duplicate rejection, unknown-criterion ignoring, and a non-empty result; verify with unit tests for valid, fenced, malformed, duplicate, and empty responses.
- [ ] 2.4 Build the prompt from `request.model_dump(mode="json", by_alias=True, exclude_none=True)`, include the five criteria, and reject payloads larger than 12,000 characters; verify prompt tests assert the caption, context, criteria, and size failure.

## 3. Reconciliation and service integration

- [ ] 3.1 Implement `reconcile_outcomes` so AI can only convert a deterministic `PASSED` semantic check into `FAILED`, never the reverse; verify unit tests cover pass→fail, fail→pass (stays failed), missing criteria, and non-semantic criteria.
- [ ] 3.2 Verify reconciliation copies `weight`, `severe`, `route`, and `data_sources` from the deterministic outcome and preserves the seven canonical criteria in order; verify with a test that compares `[o.criterio for o in reconciled]` to the `run_checks` order.
- [ ] 3.3 Wire `run_audit(request, settings, ai_auditor=None)` to build or inject the auditor only when `settings.ai_enabled`, catch all AI exceptions, append the sanitized fallback warning, and keep the existing score/verdict/routing/signing code unchanged; verify with a `FakeAuditor` that enabled mode calls `review` once and disabled mode never calls it.
- [ ] 3.4 Verify the response dict still matches the JSON v1.0 contract with AI enabled: `contract_version`, `execution_id`, `agent_id`, `status`, `error`, `started_at`/`finished_at`, and `output` keys; the seven `checks`; `data_sources`; and approval fields are present and correctly typed.
- [ ] 3.5 Verify fallback behavior for malformed JSON, timeout/exception, missing `AI_KEY` while enabled, and a safety-blocked/empty response: HTTP 200, deterministic checks, and a warning with no key, prompt, or raw provider payload.

## 4. Test isolation and regression

- [ ] 4.1 Add the autouse `_disable_external_ai` fixture to `tests/conftest.py` that sets `AUDITOR_AI_ENABLED=false` and removes `AI_KEY`; verify no existing test performs network calls.
- [ ] 4.2 Run `python3 -m pytest` and confirm all pre-existing tests plus the new tests pass with the repository `.env` present.
- [ ] 4.3 Compare a deterministic baseline response with an AI-disabled response for the documented payload and assert the JSON bodies are identical; verify with `tests/test_gemini.py`.
- [ ] 4.4 Verify that neither `response.model_dump(mode="json")` nor a trace line contains the `AI_KEY` value or the word `AI_KEY=` with a value.

## 5. Documentation and validation

- [ ] 5.1 Add `.env.example` with empty `AI_KEY`, `AUDITOR_AI_ENABLED=false`, `AUDITOR_AI_MODEL=gemini-2.5-flash`, `AUDITOR_AI_TIMEOUT_SECONDS=20`, and the existing auditor variables; verify the file contains no real credential.
- [ ] 5.2 Document in `README.md` the new variables, opt-in behavior, default model, timeout, fallback, and a local enablement example; verify the documented commands run as written.
- [ ] 5.3 Run `openspec validate add-gemini-integration --strict` and resolve every error or warning.
- [ ] 5.4 Run `python3 -m pytest -v` one final time and record the total passing count in the change summary.

## Workflow follow-up

- Review the artifacts and, when implementation is explicitly requested, run the apply workflow.
- Archive the change after implementation, tests, and review requirements are satisfied.
