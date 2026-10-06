# OpenBox CrewAI SDK

`openbox-crewai-sdk-python` adds OpenBox governance, approvals, guardrails, and OpenTelemetry-backed operational telemetry to CrewAI crews and flows. It supports per-agent IAM v3 workload authentication and legacy AIP DID request signing.

Use it when you need to:

- evaluate tasks, LLM calls, and tool operations against OpenBox policy
- enforce approval flows from OpenBox verdicts
- apply input and output guardrails
- attach HTTP, database, and file telemetry to governed runs
- enable per-agent AIP request signing when DID credentials are configured
- authenticate IAM v3 workloads using the agent's registered RSA private key

## Requirements

- Python `>=3.10,<3.14`
- `crewai >=1.15.23`
- an OpenBox account with each agent provisioned in the OpenBox Platform

When you provision an agent in the OpenBox Platform you receive:

- an OpenBox API URL
- an API key (`obx_live_...`)
- identity credentials appropriate to the agent: a registered workload RSA key for IAM v3, or an AIP DID and Ed25519 private key for legacy signing

## Installation

```bash
pip install openbox-crewai-sdk-python
```

Configure the API URL and each agent's API key. The prefix matches the `env_prefix` passed to its `OpenBoxAgent`:

```bash
export OPENBOX_URL="<your OpenBox API URL>"

export OPENBOX_RESEARCHER_API_KEY="obx_live_..."

# For IAM v3: PEM contents of the RSA key registered for this agent
export OPENBOX_RESEARCHER_WORKLOAD_PRIVATE_KEY="$(cat /path/to/workload-private-key.pem)"
```

Legacy AIP signing remains available through `OPENBOX_RESEARCHER_DID` and
`OPENBOX_RESEARCHER_PRIVATE_KEY` (a base64 Ed25519 seed), configured together.
Treat private keys with the same care as the API key.

## IAM v3 workload identity

The workload key can also be passed directly when constructing an agent:

```python
import os
from openbox import OpenBoxAgent

researcher = OpenBoxAgent(
    role="Researcher",
    goal="Research the requested topic",
    backstory="You are a research analyst.",
    env_prefix="OPENBOX_RESEARCHER",
    workload_private_key=os.environ["OPENBOX_RESEARCHER_WORKLOAD_PRIVATE_KEY"],
)
```

Resolution is the explicit argument, then `{env_prefix}_WORKLOAD_PRIVATE_KEY`,
then `OPENBOX_CREWAI_WORKLOAD_PRIVATE_KEY`, then `OPENBOX_WORKLOAD_PRIVATE_KEY`.
The value is PEM contents, not a file path. Use each agent's own registered key
when a crew contains multiple workload identities. Empty or invalid configured
keys raise `OpenBoxConfigError` instead of falling through to another source.

At `engine.govern(crew)`, the SDK validates the key locally, fetches
`/api/v3/auth/bootstrap`, and exchanges an RSA-signed `private_key_jwt` for a
short-lived Keycloak token. Validation, governance evaluation, and approval
requests then use the `/api/v3` endpoints with the API key in `Authorization`
and the token in `X-OpenBox-Workload-Token`. OpenBox-, Okta-, and Entra-managed
workload identities follow the same contract. Tokens are isolated by agent
credentials, cached for at most five minutes, and refreshed before expiry.
Sync and async execution share the cache; SDK-owned auth traffic is excluded
from instrumentation and body capture.

Authentication failures always raise `OpenBoxAuthError`, including when
`governance_policy="fail_open"`. Its subclass `OpenBoxSigningError` retains
Core's `reason_code`. Invalid bootstrap/token metadata raises `OpenBoxConfigError`;
bootstrap/token outages raise `OpenBoxNetworkError`. These errors propagate
through lifecycle events, operation hooks, delegation, and approval polling.

For rolling upgrades, bootstrap HTTP 404 or HTTP 409 with
`workload_identity_unavailable` preserves the configured legacy API-key/DID
route. Other failures do not downgrade. Without a workload key, existing
API-key/DID authentication continues to use v1.

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
- on governance API outages the configured fallback policy decides whether execution halts or continues; authentication and workload setup failures always raise

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
