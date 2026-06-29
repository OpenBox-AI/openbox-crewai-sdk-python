# Approvals and Guardrails

## Verdicts

| Verdict | Effect |
| --- | --- |
| `ALLOW` | continue |
| `REQUIRE_APPROVAL` | poll for approval if `hitl_enabled`, else `on_fallback` |
| `BLOCK` | raise `GovernanceHaltError` (Layer 1/2) or `GovernanceBlockedError` (Layer 3) |
| `HALT` | raise `GovernanceHaltError`; subsequent tasks on the same agent short-circuit |

Legacy strings are normalized and unknown values fall back to `ALLOW` with a warning. See [api-reference.md — Verdicts](./api-reference.md#verdicts) for the alias mapping.

## Errors

| Class | When |
| --- | --- |
| `GovernanceHaltError` | `BLOCK`/`HALT` at task boundary |
| `GovernanceBlockedError` | `BLOCK`/`HALT` at Layer 3 hook (HTTP/DB/file) |
| `GovernanceAPIError` | API failure with `governance_policy=fail_closed` |
| `GuardrailsValidationError` | `guardrails_result.validation_passed=false` |
| `GovernanceApprovalExpiredError` | approval window expired |

All inherit from `OpenBoxError` and are registered as CrewAI passthroughs (no retries).

## Guardrail redaction

OpenBox responses can carry `guardrails_result`:

- `input_type="activity_input"` + `redacted_input` → SDK applies redaction to `task.description` before execution
- `input_type="activity_output"` + `redacted_input` → SDK applies redaction to the result before return
- `validation_passed=false` → SDK raises `GuardrailsValidationError`

## Policy-before-guardrails caveat

Core evaluates policy **before** guardrails. If policy returns a non-`ALLOW` verdict, guardrails for that event may not run. If a guardrail you expect to fire doesn't, check the policy verdict first.

## Layer 3 swallowing caveat

When Layer 3 raises `GovernanceBlockedError` from inside a CrewAI tool, the framework catches it, hands the error string to the LLM as a tool result, the agent retries, and the LLM gate (False after halt) raises `ValueError("LLM call blocked by before_llm_call hook")`. The user-visible exception is `ValueError`, not `GovernanceBlockedError`.

If you want a clean `GovernanceHaltError`, write the policy to fire at Layer 1 against `activity_input.description` for the same trigger condition. Keep a Layer 3 fallback for defence-in-depth.

## OPA matching shortlist

| Boundary | Match against |
| --- | --- |
| `ActivityStarted` | `input.activity_input[*].description` |
| `ActivityCompleted` | `input.activity_output.result` |
| DB hook | `input.spans[*].attributes["db.operation"]` |
| File hook | `input.spans[*].name == "file.write"` plus `span.file_path` |
