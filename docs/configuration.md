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

`{PREFIX}` is each agent's `env_prefix`. DID and private key must be configured together or both omitted.

## API failure policy

`governance_policy`:

- `fail_open` — network error → `verdict=ALLOW, fallback_used=true`, execution continues
- `fail_closed` — network error → raises `GovernanceAPIError`

`on_fallback` (when Core itself returns `fallback_used=true`):

- `log_warning` — accept the verdict
- `fail_closed` — override to `BLOCK`

## Approvals

When verdict is `REQUIRE_APPROVAL`:

- `hitl_enabled=True` → SDK polls `/api/v1/governance/approval` at `hitl_poll_interval` until resolved
- `hitl_enabled=False` or crew in `exclude_crews_hitl` → treated as `on_fallback` behaviour

The polling loop runs until Core returns `allow`/`block`/`halt`, the approval expires (`GovernanceApprovalExpiredError`), or the request errors.
