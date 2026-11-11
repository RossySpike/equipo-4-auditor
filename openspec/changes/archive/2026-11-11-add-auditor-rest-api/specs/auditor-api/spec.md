# Spec Delta

## Purpose

Expose the AG-AUD auditor as a synchronous HTTP/JSON service so the orchestrator can audit generated content, obtain a signed approval, and route rejected content back to the responsible agent.

## ADDED Requirements

### Requirement: JSON v1.0 invocation contract

The service SHALL accept `POST /invoke` requests using the section JSON v1.0 envelope and SHALL reject requests that do not carry `contract_version` `1.0` or `agent_id` `AG-AUD`.

#### Scenario: Valid audit invocation

- **WHEN** a request contains `contract_version` `"1.0"`, `agent_id` `"AG-AUD"`, a non-empty `execution_id`, `step`, `iteration`, and an `input` with `brief`, `borrador`, `copy`, and `context_pack`
- **THEN** the service responds with HTTP 200 and a response envelope containing `contract_version`, `execution_id`, `agent_id`, `status`, `output`, `data_sources`, `warnings`, `error`, `started_at`, and `finished_at`

#### Scenario: Unsupported contract version

- **WHEN** a request declares a `contract_version` other than `"1.0"`
- **THEN** the service responds with HTTP 422 and does not execute any audit check

#### Scenario: Wrong agent identifier

- **WHEN** a request declares an `agent_id` other than `"AG-AUD"`
- **THEN** the service responds with HTTP 422 and does not execute any audit check

### Requirement: Liveness endpoint

The service SHALL expose a health endpoint so the orchestrator can verify availability before invoking the auditor.

#### Scenario: Health check

- **WHEN** a client sends `GET /health`
- **THEN** the service responds with HTTP 200 and a JSON body whose `status` is `"ok"` and whose `agent_id` is `"AG-AUD"`

### Requirement: Seven-check audit matrix

The service SHALL evaluate exactly seven named checks and include each result in `output.checks[]` with `criterio`, `resultado`, and `evidencia`.

#### Scenario: All checks reported

- **WHEN** any valid audit invocation is processed
- **THEN** `output.checks[]` contains results for spelling/grammar/clarity, text-image-idea coherence, commercial fact veracity, strategic/audience alignment, brand voice and tone, unsupported-claim risk, and Instagram technical limits, in that order

#### Scenario: Check outcome values

- **WHEN** a check is evaluated
- **THEN** its `resultado` is either `"PASSED"` or `"FAILED"`

### Requirement: Spelling, grammar, and clarity check

The service SHALL fail the spelling/grammar/clarity check when the caption is empty, contains placeholder markers, has unbalanced delimiters, or contains malformed spacing or punctuation.

#### Scenario: Malformed caption

- **WHEN** the caption contains an empty value, an unbalanced parenthesis, a placeholder such as `TODO`, or doubled spaces before punctuation
- **THEN** the check result is `FAILED` and its evidence names the detected problem

#### Scenario: Clean caption

- **WHEN** the caption is non-empty and contains no detected spelling, delimiter, placeholder, or spacing problem
- **THEN** the check result is `PASSED`

### Requirement: Text-image-idea coherence check

The service SHALL fail the coherence check when the visual description, the brief objective, and the caption share no meaningful content word, or when the visual description is empty.

#### Scenario: Aligned content

- **WHEN** the caption and the combination of `alt_text` and brief objective share at least one meaningful content word
- **THEN** the check result is `PASSED`

#### Scenario: Unrelated content

- **WHEN** the caption shares no meaningful content word with `alt_text` and the brief objective, or `alt_text` is empty
- **THEN** the check result is `FAILED` and its evidence explains the missing alignment

### Requirement: Commercial fact veracity check

The service SHALL verify every numeric or commercial claim in the caption and every `facts_used` identifier against the `context_pack` source of truth, and SHALL mark any unverifiable claim as failed.

#### Scenario: Claim supported by context

- **WHEN** a percentage, price, or referenced fact identifier in the content appears in `context_pack.facts` or `context_pack.promociones`
- **THEN** the check result is `PASSED` for that claim

#### Scenario: Invented commercial claim

- **WHEN** the caption contains a percentage or price that does not appear in `context_pack.facts` or `context_pack.promociones`
- **THEN** the check result is `FAILED`, a `hallazgo` describes the unverified claim, and the check is treated as severe

#### Scenario: Unknown fact reference

- **WHEN** `context_pack.facts` supplies a fact catalog and `copy.facts_used` contains an identifier that does not exist in it
- **THEN** the check result is `FAILED` and a `hallazgo` names the unknown identifier

### Requirement: Strategic and audience alignment check

The service SHALL fail the strategic alignment check when the caption contains no call to action or shares no meaningful content with the brief objective.

#### Scenario: Caption includes a call to action

- **WHEN** the caption contains an actionable call to action such as buying, visiting, ordering, or reserving
- **THEN** the check result is `PASSED`

#### Scenario: Caption lacks a call to action

- **WHEN** the caption contains no recognized call to action and no meaningful content word from the brief objective
- **THEN** the check result is `FAILED` and its evidence reports the missing call to action

### Requirement: Brand voice and tone check

The service SHALL fail the brand voice check when the content uses a prohibited term declared in `context_pack` or when the caption is written entirely in upper case beyond a short emphasis token.

#### Scenario: Prohibited term present

- **WHEN** the caption contains a term listed in `context_pack.palabras_prohibidas` or in the brand rules
- **THEN** the check result is `FAILED` and a `hallazgo` names the prohibited term

#### Scenario: Brand-safe content

- **WHEN** the caption contains no prohibited term and is not entirely upper case
- **THEN** the check result is `PASSED`

### Requirement: Unsupported-claim risk check

The service SHALL fail the risk check when the content makes absolute or deceptive promises that are not supported by the `context_pack` source of truth.

#### Scenario: Absolute promise detected

- **WHEN** the caption contains an absolute claim such as a guaranteed result, `100%`, "the best", "always", "never", or "risk-free" that is absent from `context_pack`
- **THEN** the check result is `FAILED`, a `hallazgo` quotes the claim, and the check is treated as severe

#### Scenario: No absolute promise

- **WHEN** the caption contains no absolute or deceptive promise, or any such claim is explicitly present in the `context_pack`
- **THEN** the check result is `PASSED`

### Requirement: Instagram technical limits check

The service SHALL enforce Instagram platform limits: caption length at most 2,200 characters, at most 30 hashtags, well-formed hashtag tokens, and a supported image format when the asset URL exposes a file extension.

#### Scenario: Caption exceeds limit

- **WHEN** the caption exceeds 2,200 characters
- **THEN** the limits check is `FAILED`, a `hallazgo` reports the caption length, and the check is treated as severe

#### Scenario: Too many hashtags

- **WHEN** more than 30 hashtags are supplied, or a hashtag does not begin with `#` or contains whitespace
- **THEN** the limits check is `FAILED` and a `hallazgo` reports the hashtag problem

#### Scenario: Unsupported image format

- **WHEN** the `asset_url` path ends in an extension outside `jpg`, `jpeg`, `png`, or `webp`
- **THEN** the limits check is `FAILED` and a `hallazgo` reports the unsupported format

#### Scenario: Content within platform limits

- **WHEN** the caption is at most 2,200 characters, hashtags are at most 30 and well formed, and the asset format is supported or has no extension
- **THEN** the limits check is `PASSED`

### Requirement: Verdict and score calculation

The service SHALL compute a 0-100 score from the failed checks and SHALL emit `APROBADO` only when the score is at least the configured threshold and no severe check failed; otherwise it SHALL emit `RECHAZADO`.

#### Scenario: Approval

- **WHEN** the score is at least the configured threshold and the severe veracity, risk, and Instagram-limit checks all passed
- **THEN** `output.veredicto` is `"APROBADO"` and the response `status` is `"completed"`

#### Scenario: Score below threshold

- **WHEN** the computed score is below the configured threshold
- **THEN** `output.veredicto` is `"RECHAZADO"`, the response `status` is `"rejected"`, and `output.hallazgos` is non-empty

#### Scenario: Severe check failure

- **WHEN** the veracity, risk, or Instagram-limit check fails even though the score reaches the threshold
- **THEN** `output.veredicto` is `"RECHAZADO"` and a `hallazgo` identifies the severe failure

### Requirement: Return routing

The service SHALL set `output.return_to` on rejection to `AG-EST`, `AG-BOR`, or `AG-COP` according to the failing check, and SHALL set it to `null` on approval.

#### Scenario: Strategy failure routes to strategist

- **WHEN** the strategic alignment check fails and no higher-priority routing failure exists
- **THEN** `return_to` is `"AG-EST"`

#### Scenario: Visual coherence failure routes to draft creator

- **WHEN** the coherence check fails and no strategy failure exists
- **THEN** `return_to` is `"AG-BOR"`

#### Scenario: Copy failure routes to copywriter

- **WHEN** spelling, veracity, tone, risk, or Instagram-limit checks fail without a strategy or visual failure
- **THEN** `return_to` is `"AG-COP"`

#### Scenario: Approved content has no return target

- **WHEN** the verdict is `APROBADO`
- **THEN** `return_to` is `null`

### Requirement: Approval identifier and signed content hash

On approval the service SHALL generate a unique `approval_id`, compute a SHA-256 `content_hash` over the canonical concatenation of `asset_url`, `caption`, and hashtags, and produce an HMAC-SHA256 signature that a publisher can verify.

#### Scenario: Signed approval

- **WHEN** the verdict is `APROBADO`
- **THEN** `output.approval_id` matches `APP-<YYYYMMDD>-<8 hexadecimal characters>`, `output.content_hash` is a 64-character lowercase hexadecimal SHA-256 digest, and `output.content_signature` is a valid HMAC-SHA256 of that digest using the configured signing key

#### Scenario: Rejected content is not approved

- **WHEN** the verdict is `RECHAZADO`
- **THEN** `approval_id`, `content_hash`, and `content_signature` are `null`

#### Scenario: Hash determinism

- **WHEN** the same `asset_url`, `caption`, and ordered hashtag list are audited twice
- **THEN** both evaluations produce the same `content_hash`

### Requirement: Audit trace persistence

The service SHALL append one JSON-line trace record per invocation containing the execution identifier, iteration, step, agent identifier, input payload, output payload, data sources, status, and start/finish timestamps.

#### Scenario: Trace written after audit

- **WHEN** an invocation completes, whether approved or rejected
- **THEN** the configured trace file contains exactly one new JSON line whose `execution_id`, `iteration`, `step`, `agent_id`, `input_payload`, `output_payload`, `data_sources`, `status`, `started_at`, and `finished_at` match the processed invocation

### Requirement: Source references in the response

The service SHALL report the `context_pack` entities used as audit evidence in `data_sources`, using `source` and `ref` pairs.

#### Scenario: Commercial source cited

- **WHEN** a commercial claim is verified against a promotion or fact
- **THEN** `data_sources` includes an entry whose `source` names the context entity and whose `ref` identifies the matched promotion or fact

#### Scenario: No external source used

- **WHEN** the content makes no commercial claim and no source entity was consulted
- **THEN** `data_sources` is an empty list
