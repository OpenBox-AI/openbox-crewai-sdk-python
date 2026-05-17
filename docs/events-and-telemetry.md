# Events and Telemetry

The SDK emits two kinds of payload to `/api/v1/governance/evaluate`: **boundary events** at task start/end, and **hook payloads** from HTTP / DB / file telemetry.

## Event types

| Event | When | Layer |
| --- | --- | --- |
| `WorkflowStarted` | first task per agent per kickoff | session open |
| `WorkflowCompleted` | crew cleanup or kickoff-start drain | session close (`status=completed` or `halted`) |
| `ActivityStarted` | before each governed task | Layer 1 |
| `ActivityCompleted` | after each governed task | Layer 2 |
| Hook payload | HTTP / DB / file operations | Layer 3 (`hook_trigger=true`, carries `spans`) |

Common payload fields: `source: "crewai-telemetry"`, `timestamp`, `workflow_id` (agent session), `run_id`, `workflow_type` (`"<role> Agent"`), `task_queue` (crew name), `agent_role`, `metadata.{crew_name, crew_execution_id, flow_execution_id?}`.

## What's instrumented

| Surface | Default | Libraries |
| --- | --- | --- |
| HTTP | always on | `httpx` (sync + async), `requests`, `urllib3` |
| Databases | `instrument_databases=True` | `psycopg2`, `asyncpg`, `mysql-connector-python`, `pymysql`, `pymongo`, `redis`, `sqlalchemy` |
| File I/O | `instrument_file_io=False` | wrapt patch on `open()` |
| LLM gate | `llm_level_governance=True` | CrewAI before-LLM-call hook (no spans, just block-on-halt) |

Restrict DB instrumentation with `db_libraries={"psycopg2", "asyncpg"}`. File I/O skips `/dev/`, `/proc/`, `/sys/`, `__pycache__`, `.pyc`, `.pyo`, `.so`, `.dylib`. HTTP body capture is text-only (`text/*`, `application/json`, `application/xml`, `application/javascript`, `application/x-www-form-urlencoded`).

## Identity

- `workflow_id` / `run_id` are UUIDs per agent per kickoff (fresh on every kickoff after cleanup).
- `metadata.crew_execution_id` is a UUID per crew kickoff.
- `metadata.flow_execution_id` is present only when running inside a `create_openbox_flow()`-wrapped flow.

## Activity payload shape

`ActivityStarted.activity_input`:

```json
[
  {"description": "..."},
  {"expected_output": "..."}
]
```

`ActivityCompleted.activity_output`:

```json
{"result": "..."}
```

`activity_type` comes from `OpenBoxTask.activity_type` verbatim (no normalization).

## Hook payloads

Layer 3 telemetry uses `hook_trigger: true` with a `spans` array. One payload carries one phase (`stage="started"` or `"completed"`), normalized spans, and an `activity_id` linking it to the parent activity.

### DB spans

Carry both top-level fields (`db_system`, `db_operation`, `db_statement`, `db_name`) **and** OTel-style attributes (`db.system`, `db.operation`, `db.statement`, `db.name`). OPA receives the attributes verbatim but not the top-level `db_*` fields, so policies should match against attributes.

```rego
result := {"decision": "BLOCK", "reason": "..."} if {
    some span in input.spans
    span.hook_type == "db_query"
    span.stage == "started"
    upper(object.get(span.attributes, "db.operation", "")) in {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE"}
}
```

### File spans

`name` is `file.read`, `file.write`, `file.open`, or `file.delete`. Each operation gets a unique `span_id` so Core's hook deduplication doesn't collide across operations on the same file.

### Ignored URLs

The engine adds its own `api_url` to the ignored set automatically. The SDK doesn't trace its own API traffic.

### Body capture isolation

HTTP request and response bodies are buffered inside the SDK's body capture layer, not as OTel span attributes. Generic OTel exporters won't carry them. Bodies are merged into governance payloads only when needed and re-checked for content-type before inclusion.

## Sequences

Single task:

```
WorkflowStarted -> ActivityStarted -> hook payloads -> ActivityCompleted -> WorkflowCompleted
```

Halted (BLOCK at Layer 1):

```
WorkflowStarted -> ActivityStarted (verdict=BLOCK -> raise GovernanceHaltError)
remaining tasks short-circuit -> WorkflowCompleted (status=halted)
```

## Policy guidance

- govern `ActivityStarted`/`ActivityCompleted` for business decisions
- treat hook payloads as internal telemetry by default — gating them as if they were business actions creates duplicate approvals and noisy timelines
