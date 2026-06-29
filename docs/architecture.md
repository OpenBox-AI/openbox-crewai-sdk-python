# Architecture

## Layout

```
CrewAI app  →  OpenBoxEngine  →  GovernedCrew  →  OpenBoxAgent (per-agent governance)
                  │                                       │
                  ├─ GovernanceClient (HTTP + retries)    │
                  ├─ GovernanceConfig                     │
                  ├─ GovernanceSpanProcessor              │
                  ├─ OTel instrumentation                 │
                  │  (HTTP / DB / file / LLM gate)        │
                  └─ AIP request signing  ────────────────┘
                                       │
                                       ▼
                                OpenBox Core
                                  /api/v1/auth/validate
                                  /api/v1/governance/evaluate
                                  /api/v1/governance/approval
```

## Components

| Component | Responsibility |
| --- | --- |
| `OpenBoxEngine` | owns the governance client, OTel setup, and span processor for the process; `engine.govern(crew)` binds crews |
| `GovernedCrew` | manages the governance lifecycle around `kickoff()` / `akickoff()`: opens and closes per-agent sessions, recovers from prior crashed runs, and propagates governance verdicts as exceptions |
| `OpenBoxAgent` | resolves credentials from `env_prefix`, runs Layer 1 / Layer 2 evaluation around each task, and emits `WorkflowStarted` lazily on the first task |
| Governance client (internal) | HTTP requests to Core with retry, timeout, AIP signing, and fallback handling |
| Span processor (internal) | buffers spans per trace and routes Layer 3 hooks to the governance bridge |

## Boundary vs telemetry

The SDK distinguishes:

- **business boundary events** — `ActivityStarted`/`ActivityCompleted` per task, `WorkflowStarted`/`WorkflowCompleted` per agent session
- **internal telemetry** — Layer 3 hook payloads for HTTP, DB, file, and LLM-gate events

Policy is easiest to write at boundary events. Treating hook payloads as if they were business actions creates duplicate approvals.

## Per-run lifecycle

When `governed_crew.kickoff()` runs:

1. any session left open by a prior crashed run is closed (`WorkflowCompleted` is emitted) and per-agent state is reset
2. governance config is wired into each agent
3. CrewAI runs the configured `Process`
4. on completion or failure, sessions are closed and per-agent state is reset — the same crew is safe to reuse for a fresh kickoff

## Usage rules

- one `OpenBoxEngine` per process; close it on shutdown (or use `with`)
- a second engine in the same process with different instrumentation settings raises
- govern any number of crews against a single engine
- `engine.govern(crew)` is idempotent on a given crew
- pair `OpenBoxAgent` with `OpenBoxTask` — a plain `Task` assigned to an `OpenBoxAgent` raises `OpenBoxConfigError`
- a plain `Agent` in the same crew is allowed (warns) and emits no governance events
- a hierarchical crew's `manager_agent` is governed when it is an `OpenBoxAgent`

### Multi-crew correlation

When a CrewAI `Flow` orchestrates multiple governed crews, wrap the flow with `create_openbox_flow(FlowClass)` to share a `flow_execution_id` across their metadata. The flow itself is not governed — only correlation. See [api-reference.md](./api-reference.md#create_openbox_flowflow_class-flow_kwargs).

## Failure model

| Failure | Surface |
| --- | --- |
| OpenBox API unreachable | `GovernanceAPIError` (`fail_closed`) or soft-allow with `fallback_used=true` (`fail_open`) |
| `BLOCK`/`HALT` at Layer 1/2 | `GovernanceHaltError` |
| `BLOCK`/`HALT` at Layer 3 | `GovernanceBlockedError` (often surfaces as `ValueError` after CrewAI tool retry — see [approvals-and-guardrails.md](./approvals-and-guardrails.md)) |
| Approval expired | `GovernanceApprovalExpiredError` |
| Underlying CrewAI failure | unchanged |
