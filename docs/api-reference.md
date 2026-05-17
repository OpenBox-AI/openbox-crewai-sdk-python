# API Reference

## Imports

```python
from openbox import (
    OpenBoxAgent,
    OpenBoxTask,
    OpenBoxEngine,
    GovernedCrew,
    GovernanceConfig,
    Verdict,
    GovernanceResponse,
    create_openbox_engine,
    create_openbox_flow,
)

from openbox import (
    OpenBoxError,
    OpenBoxConfigError,
    OpenBoxAuthError,
    OpenBoxNetworkError,
    OpenBoxInsecureURLError,
    GovernanceAPIError,
    GovernanceHaltError,
    GovernanceBlockedError,
    GovernanceApprovalExpiredError,
)
```

Treat names re-exported from `openbox` as the public surface. Subpath imports (`openbox.core.*`, `openbox.crewai.*`, `openbox.instrumentation.*`) are internal.

## Entry points

### `create_openbox_engine(...)`

```python
engine = create_openbox_engine(
    api_url=None,                       # OPENBOX_URL env if None
    config=None,                        # if set, ignores the kwargs below
    governance_timeout=30.0,
    governance_policy="fail_open",      # or "fail_closed"
    on_fallback="log_warning",          # or "fail_closed"
    send_task_start_event=True,
    send_task_completed_event=True,
    llm_level_governance=True,
    hitl_enabled=True,
    hitl_poll_interval=5.0,
    exclude_crews_hitl=None,
    instrument_databases=True,
    db_libraries=None,
    instrument_file_io=False,
    debug_log=False,
)
```

Returns `OpenBoxEngine`. Use as a context manager.

### `OpenBoxEngine`

| Member | Purpose |
| --- | --- |
| `engine.govern(crew)` | bind a `Crew` (or `GovernedCrew`); returns the `GovernedCrew` |
| `engine.config` | resolved `GovernanceConfig` |
| `engine.close()` | tear down instrumentation and HTTP clients |

`OpenBoxEngine` is a context manager — use `with create_openbox_engine() as engine:` so `close()` runs on exit.

### `OpenBoxAgent`

Subclass of CrewAI `Agent`. Required field: `env_prefix: str` — drives `{PREFIX}_API_KEY`, `{PREFIX}_DID`, `{PREFIX}_PRIVATE_KEY` lookup.

### `OpenBoxTask`

Subclass of CrewAI `Task`. Required field: `activity_type: str` — passed verbatim into governance payloads. Plain `Task` assigned to an `OpenBoxAgent` raises `OpenBoxConfigError`.

### `GovernedCrew`

Subclass of CrewAI `Crew` returned by `engine.govern(crew)`. Override of `kickoff()` / `akickoff()` runs the governance lifecycle.

### `create_openbox_flow(flow_class, **flow_kwargs)`

Wraps a CrewAI `Flow` so its `kickoff()`/`akickoff()` set a `flow_execution_id` `ContextVar` for the duration of the run. Every governed crew kickoff inside the flow includes that ID in metadata. The flow itself is not governed.

## Verdicts

```python
Verdict.ALLOW
Verdict.REQUIRE_APPROVAL
Verdict.BLOCK
Verdict.HALT
```

| Method | Purpose |
| --- | --- |
| `verdict.should_stop()` | `True` for `BLOCK`/`HALT` |
| `verdict.requires_approval()` | `True` for `REQUIRE_APPROVAL` |
| `Verdict.from_string(value)` | parse a string verdict; unknown values → `ALLOW` with a warning |
| `Verdict.highest_priority(*verdicts)` | strongest verdict from a list |

Legacy aliases accepted by `Verdict.from_string`:

| Legacy string | Maps to |
| --- | --- |
| `continue` | `ALLOW` |
| `stop` | `BLOCK` |
| `require-approval` | `REQUIRE_APPROVAL` |

## `GovernanceResponse`

Fields: `verdict`, `reason`, `policy_id`, `risk_score`, `governance_event_id`, `guardrails_result`, `approval_id`, `fallback_used`.

When `fallback_used=True` and `on_fallback="fail_closed"`, the SDK overrides the verdict to `BLOCK` after parsing.

## Errors

| Class | When |
| --- | --- |
| `OpenBoxConfigError` | invalid configuration |
| `OpenBoxAuthError` | `/api/v1/auth/validate` returns 401/403 |
| `OpenBoxNetworkError` | engine cannot reach Core |
| `OpenBoxInsecureURLError` | non-localhost `OPENBOX_URL` is HTTP |
| `GovernanceAPIError` | API failure with `governance_policy="fail_closed"` |
| `GovernanceHaltError` | `BLOCK`/`HALT` at task boundary; carries `verdict`, `reason`, `policy_id` |
| `GovernanceBlockedError` | `BLOCK`/`HALT` at Layer 3 hook; carries `hook_type` |
| `GovernanceApprovalExpiredError` | approval window expired |

All inherit from `OpenBoxError` and are CrewAI passthroughs (no retries).

## Example

```python
from crewai import Crew, Process
from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

with create_openbox_engine() as engine:
    agent = OpenBoxAgent(
        role="Researcher",
        goal="Find information",
        env_prefix="OPENBOX_RESEARCHER",
    )
    task = OpenBoxTask(
        description="Research RAG.",
        expected_output="Notes.",
        agent=agent,
        activity_type="research",
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential)
    result = engine.govern(crew).kickoff()
```

Async equivalent: `await engine.govern(crew).akickoff()`.
