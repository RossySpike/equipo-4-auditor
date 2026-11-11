# Spec Delta

## ADDED Requirements

### Requirement: AI credential and activation settings

The service SHALL read the Gemini credential from the `AI_KEY` variable in the repository `.env` file, SHALL let process environment variables override that file, and SHALL keep AI review disabled unless `AUDITOR_AI_ENABLED` is explicitly true.

#### Scenario: Credential loaded from .env

- **WHEN** the repository `.env` contains `AI_KEY` and the process environment does not define it
- **THEN** the resolved settings expose the credential only through a secret value and the service does not expose it in responses, traces, or logs

#### Scenario: Environment overrides .env

- **WHEN** both `.env` and the process environment define `AI_KEY`
- **THEN** the process environment value takes precedence

#### Scenario: AI explicitly disabled

- **WHEN** `AI_KEY` is present but `AUDITOR_AI_ENABLED` is absent or false
- **THEN** the service performs no Gemini call and produces the deterministic audit response

#### Scenario: AI enabled without credential

- **WHEN** `AUDITOR_AI_ENABLED` is true and `AI_KEY` is missing or blank
- **THEN** the service responds successfully using the deterministic audit and includes a warning that the credential is not configured

### Requirement: Opt-in semantic review with Gemini

When AI review is enabled, the service SHALL request a Gemini review for the semantic checks (spelling, coherence, strategy, brand, and risk) and SHALL NOT delegate commercial veracity or Instagram technical limits to the model.

#### Scenario: Enabled semantic review

- **WHEN** AI review is enabled and the credential is present
- **THEN** the service sends the audit input to Gemini exactly once per invocation and obtains results for the five semantic checks

#### Scenario: Objective checks remain deterministic

- **WHEN** any invocation is processed with AI review enabled
- **THEN** commercial veracity and Instagram technical limits are evaluated by the existing deterministic rules and their evidence is not replaced by model output

### Requirement: Bounded and structured Gemini invocation

The service SHALL invoke the configured Gemini model with deterministic generation settings, a bounded timeout, a prompt containing the audit request data, and a JSON-only response format.

#### Scenario: Model invocation profile

- **WHEN** the service builds a Gemini request
- **THEN** it uses model `gemini-2.5-flash` by default, temperature `0`, a single candidate, a configured timeout, and requests a JSON response

#### Scenario: Prompt contains the audit data

- **WHEN** the service builds the Gemini prompt
- **THEN** the prompt includes the brief, draft, copy, and business context of the invocation, the five semantic criteria, and instructions not to invent commercial facts

### Requirement: Contract-preserving reconciliation

The service SHALL reconcile Gemini results with the deterministic outcomes so that the response keeps the seven checks in their documented order with `criterio`, `resultado`, and `evidencia`, while score, verdict, routing, data sources, and approval fields remain derived from deterministic rules.

#### Scenario: Gemini finds an additional semantic problem

- **WHEN** a semantic check passes deterministically but Gemini reports `FAILED` with evidence
- **THEN** that check is reported as `FAILED`, its deterministic weight and severity apply, and the score and verdict are recomputed with the existing rules

#### Scenario: Gemini approves a deterministic failure

- **WHEN** Gemini reports `PASSED` for a check that the deterministic engine marked `FAILED`
- **THEN** the check remains `FAILED` and the deterministic evidence and finding are preserved

#### Scenario: Response shape is preserved

- **WHEN** any AI-assisted invocation completes
- **THEN** the response contains exactly the seven documented criteria in order and the existing approval, signature, routing, data-source, warning, error, and timestamp fields

### Requirement: Deterministic fallback on AI failure

The service SHALL fall back to the deterministic audit and continue with HTTP 200 whenever the Gemini call fails, times out, is blocked, or returns an invalid payload.

#### Scenario: Invalid or failed model response

- **WHEN** Gemini returns malformed JSON, omits required fields, times out, exceeds quota, or is blocked by safety filters
- **THEN** the service discards the model result, uses the deterministic seven-check outcome, and adds a warning that AI review was unavailable

#### Scenario: Credential never leaks on failure

- **WHEN** an AI failure is reported in warnings or logs
- **THEN** the message contains no `AI_KEY` value, prompt contents, or raw provider payload
