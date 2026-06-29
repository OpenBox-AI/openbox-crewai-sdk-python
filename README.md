# OpenBox CrewAI SDK

`openbox-crewai-sdk-python` adds OpenBox governance, approvals, guardrails, and OpenTelemetry-backed operational telemetry to CrewAI crews and flows. When an agent is configured with an AIP DID and private key, governance requests are signed per agent.

Use it when you need to:

- evaluate tasks, LLM calls, and tool operations against OpenBox policy
- enforce approval flows from OpenBox verdicts
- apply input and output guardrails
- attach HTTP, database, and file telemetry to governed runs
- enable per-agent AIP request signing when DID credentials are configured

## Requirements

- Python `>=3.10,<3.14`
- `crewai >=1.14.1`
- an OpenBox account with each agent provisioned in the OpenBox Platform

When you provision an agent in the OpenBox Platform you receive:

- an OpenBox API URL
- an API key (`obx_live_...`)
- an AIP DID and one-time private key

## Installation

```bash
pip install openbox-crewai-sdk-python
```

Required environment variables (one set per agent — the prefix matches the `env_prefix` passed to each `OpenBoxAgent`):

```bash
export OPENBOX_URL="<your OpenBox API URL>"

export OPENBOX_RESEARCHER_API_KEY="obx_live_..."
export OPENBOX_RESEARCHER_DID="did:aip:..."
export OPENBOX_RESEARCHER_PRIVATE_KEY="<base64 32-byte ed25519 seed>"
```

The DID and private key are returned once at agent creation and cannot be recovered later. If you enable AIP signing, store them with the same care as the API key.

## Quick Start

```python
from crewai import Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine


@CrewBase
class ResearchCrew:
    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def researcher(self) -> OpenBoxAgent:
        return OpenBoxAgent(
            config=self.agents_config["researcher"],  # type: ignore[index]
            env_prefix="OPENBOX_RESEARCHER",
        )

    @task
    def quick_research(self) -> OpenBoxTask:
        return OpenBoxTask(
            config=self.tasks_config["quick_research"],  # type: ignore[index]
            activity_type="research",
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            name="research-crew",
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
        )


with create_openbox_engine() as engine:
    governed_crew = engine.govern(ResearchCrew().crew())
    result = governed_crew.kickoff()
```

`create_openbox_engine()` is the recommended production entrypoint. It:

1. resolves and validates SDK configuration
2. constructs the shared OpenBox runtime and governance config
3. installs HTTP, database, and (opt-in) file-IO instrumentation
4. yields an engine that wraps standard CrewAI crews via `engine.govern(crew)`
5. tears down instrumentation on context-manager exit

`OpenBoxAgent`, `OpenBoxTask`, and `engine.govern(crew)` opt a crew in to governance. Plain `Agent` and `Task` instances skip governance.

## Runtime Model

The SDK emits two categories of OpenBox payloads:

- boundary task events: `ActivityStarted`, `ActivityCompleted`
- signal events: `SignalReceived` for HITL resume and agent output

It also captures operational spans for:

- HTTP requests
- supported database libraries
- file operations when file instrumentation is enabled
- LLM calls

Important production behavior:

- when DID credentials are configured, governance requests are signed with the agent's AIP DID and verified by OpenBox before evaluation
- on OpenBox API errors the configured fallback policy decides whether execution halts or continues

## Configuration Highlights

Most applications only need a small part of the config surface:

| Option | Default | Use it to |
| --- | --- | --- |
| `api_url` | required | point the SDK at your OpenBox API URL |
| `governance_policy` | `"fail_open"` | decide whether OpenBox outages should halt execution |
| `governance_timeout` | `30.0` | cap the time the SDK waits for an OpenBox verdict |
| `hitl_enabled` | `True` | enable approval suspension and polling flows |
| `send_task_start_event` | `True` | emit `ActivityStarted` |
| `send_task_completed_event` | `True` | emit `ActivityCompleted` |
| `instrument_databases` | `True` | capture supported database activity |
| `instrument_file_io` | `False` | enable file operation telemetry when required |
| `debug_log` | `False` | verbose SDK logging |

See [docs/configuration.md](./docs/configuration.md) for the complete surface.

## Production Guidance

- Decide explicitly between `fail_open` and `fail_closed` before deployment.
- Give every agent its own `env_prefix` — never share API keys or DIDs across agents.
- If you enable AIP signing, treat the private key with the same care as the API key. It is returned once at provisioning and is unrecoverable; rotate it in the OpenBox Platform if compromised.
- Keep `instrument_file_io` disabled until you have a concrete file-governance requirement.

## Documentation

- [docs/installation.md](./docs/installation.md): installation, startup, and shutdown
- [docs/configuration.md](./docs/configuration.md): configuration surface, env vars, and defaults
- [docs/architecture.md](./docs/architecture.md): runtime architecture and data flow
- [docs/events-and-telemetry.md](./docs/events-and-telemetry.md): event types, HTTP/DB/file capture, signals
- [docs/approvals-and-guardrails.md](./docs/approvals-and-guardrails.md): verdict enforcement, approvals, and guardrails
- [docs/security-and-privacy.md](./docs/security-and-privacy.md): transport, AIP signing, capture boundaries
- [docs/troubleshooting.md](./docs/troubleshooting.md): common failures and diagnostics
- [docs/api-reference.md](./docs/api-reference.md): public API summary

## Public API Summary

Top-level exports include:

- `create_openbox_engine()` and `create_openbox_flow()`
- `OpenBoxAgent`, `OpenBoxTask`, `GovernedCrew`, `OpenBoxEngine`
- `GovernanceConfig`, `GovernanceResponse`, `Verdict`
- error types: `OpenBoxError`, `OpenBoxConfigError`, `OpenBoxAuthError`, `OpenBoxNetworkError`, `OpenBoxInsecureURLError`, `GovernanceAPIError`, `GovernanceHaltError`, `GovernanceBlockedError`, `GovernanceApprovalExpiredError`

See [docs/api-reference.md](./docs/api-reference.md) for the full reference.
