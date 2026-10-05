# Configuration

## `create_openbox_engine()`

| Option | Default | Purpose |
| --- | --- | --- |
| `api_url` | `OPENBOX_URL` env | OpenBox Core base URL |
| `governance_timeout` | `30.0` | HTTP timeout (s) |
| `governance_policy` | `"fail_open"` | API outage policy: `fail_open` or `fail_closed` |
| `on_fallback` | `"log_warning"` | Core `fallback_used=true` policy: `log_warning` or `fail_closed` |
| `send_task_start_event` | `True` | emit `ActivityStarted` (Layer 1) |
| `send_task_completed_event` | `True` | emit `ActivityCompleted` (Layer 2) |
| `llm_level_governance` | `True` | gate LLM calls after a halt (Layer 3) |
| `hitl_enabled` | `True` | poll for approval on `REQUIRE_APPROVAL` |
| `hitl_poll_interval` | `5.0` | poll interval (s) |
| `exclude_crews_hitl` | `None` | crew names to skip HITL polling for |
| `instrument_databases` | `True` | enable DB instrumentation |
| `db_libraries` | `None` (all) | restrict to specific DB drivers |
| `instrument_file_io` | `False` | enable file I/O instrumentation |
| `debug_log` | `False` | per-agent trace logging |

## Environment variables

| Name | Required | Purpose |
| --- | --- | --- |
| `OPENBOX_URL` | yes (or `api_url`) | Core URL |
| `{PREFIX}_API_KEY` | yes per agent | per-agent OpenBox API key |
| `{PREFIX}_DID` | optional | agent DID; when set with the private key, enables AIP signing |
| `{PREFIX}_PRIVATE_KEY` | optional | Ed25519 seed (base64); paired with DID for AIP signing |
| `{PREFIX}_WORKLOAD_PRIVATE_KEY` | optional | per-agent RSA PEM key for IAM v3 workload authentication |
| `OPENBOX_CREWAI_WORKLOAD_PRIVATE_KEY` | optional | workload key fallback for this SDK |
| `OPENBOX_WORKLOAD_PRIVATE_KEY` | optional | shared workload key fallback |

`{PREFIX}` is each agent's `env_prefix`. DID and private key must be configured together or both omitted.

## Workload identity

`OpenBoxAgent(workload_private_key=...)` accepts an unencrypted RSA PEM private key
(PKCS8, at least 2048 bits). Configuration is resolved when the agent is bound or
configured for a kickoff, so environment variables may be loaded after construction.

Precedence is the explicit argument, `{PREFIX}_WORKLOAD_PRIVATE_KEY`,
`OPENBOX_CREWAI_WORKLOAD_PRIVATE_KEY`, then `OPENBOX_WORKLOAD_PRIVATE_KEY`.
An explicitly empty or malformed value is an error. A key is not inherited from
a previous kickoff after its configuration is removed. Keep global fallbacks
unset when agents should use different registered keys.

The workload key is excluded from agent serialization and representations.
The workload token header is redacted from captured HTTP metadata. Bootstrap and
token exchanges are excluded from both OTel instrumentation and SDK body capture.

IAM v3 uses `/api/v3/auth/bootstrap`, a Keycloak client-credentials exchange with
`private_key_jwt`, then `/api/v3/auth/validate`, `/api/v3/governance/evaluate`, and
`/api/v3/governance/approval`. No identity-method setting is required.
Each credential set owns its token cache; synchronous and asynchronous callers
share it, including task events, delegation, and HTTP/DB/file hooks. Tokens are
refreshed with a 30-second margin and cached for no more than 300 seconds.
Async bootstrap/refresh runs in a worker; async governance requests use the
native HTTP async client.

Bootstrap 404 and 409 with the machine code `workload_identity_unavailable` allow
the existing API-key/DID route. That absence is cached for 60 seconds before the
SDK checks again. Once workload authority is established, refresh failures never
switch requests to the legacy route.

For direct `GovernanceClient` use, pass `workload_private_key=...` on validation,
evaluation, and approval calls. Its methods do not read agent environment variables.
The `build_auth_headers` and `build_request_headers` helpers remain local helpers
for API-key/DID headers; the request methods own workload bootstrap and token exchange.

## API failure policy

`governance_policy`:

- `fail_open` — network error → `verdict=ALLOW, fallback_used=true`, execution continues
- `fail_closed` — network error → raises `GovernanceAPIError`

HTTP 401/403 always raises `OpenBoxAuthError`, regardless of this setting.
Signed/workload rejections with a machine code raise its subclass
`OpenBoxSigningError`, exposing `.reason_code`. Workload bootstrap/token outages
raise `OpenBoxNetworkError`; invalid keys or metadata raise `OpenBoxConfigError`.
None of these authentication errors becomes ALLOW or pending approval. A hook
authentication error is also retained for the agent execution boundary, preventing
framework exception handling from turning it into a successful task result.

`on_fallback` (when Core itself returns `fallback_used=true`):

- `log_warning` — accept the verdict
- `fail_closed` — override to `BLOCK`

## Approvals

When verdict is `REQUIRE_APPROVAL`:

- `hitl_enabled=True` → SDK polls `/api/v3/governance/approval` for active workload authentication, or `/api/v1/governance/approval` for legacy authentication, at `hitl_poll_interval` until resolved
- `hitl_enabled=False` or crew in `exclude_crews_hitl` → treated as `on_fallback` behaviour

The polling loop runs until Core returns `allow`/`block`/`halt`, the approval expires (`GovernanceApprovalExpiredError`), or the request errors.
