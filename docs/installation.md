# Installation

## Install

```bash
pip install openbox-crewai-sdk-python
# or
uv add openbox-crewai-sdk-python
```

## Environment variables

```bash
export OPENBOX_URL="https://your-openbox-core.example"

# Per agent — env_prefix on OpenBoxAgent drives the lookup
export OPENBOX_RESEARCHER_API_KEY="obx_live_your_key"
# IAM v3: load the registered RSA private key as PEM contents
export OPENBOX_RESEARCHER_WORKLOAD_PRIVATE_KEY="$(cat /path/to/workload-private-key.pem)"
```

Validation at `engine.govern(crew)`:

- API key matches `obx_live_*` or `obx_test_*`
- non-localhost URLs use HTTPS
- DID and private key configured together when AIP signing is enabled
- configured workload key is an RSA private key of at least 2048 bits
- workload identity bootstrapped and API key validated against `/api/v3/auth/validate`, or `/api/v1/auth/validate` for legacy authentication

A bad value raises `OpenBoxConfigError`, `OpenBoxAuthError`, or `OpenBoxNetworkError`.

Legacy DID signing uses `{PREFIX}_DID` and `{PREFIX}_PRIVATE_KEY` together instead.
See [configuration](./configuration.md#workload-identity) for key precedence,
rolling-upgrade fallback, and the `workload_private_key` agent argument.

## Minimal startup

```python
from crewai import Crew, Process
from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

with create_openbox_engine() as engine:
    agent = OpenBoxAgent(
        role="Researcher",
        goal="Find information",
        backstory="You are a research analyst.",
        env_prefix="OPENBOX_RESEARCHER",
    )
    task = OpenBoxTask(
        description="Research RAG techniques.",
        expected_output="A 200-word summary.",
        agent=agent,
        activity_type="research",
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential)
    governed = engine.govern(crew)
    print(governed.kickoff())
```

The context manager closes the engine on exit, tearing down OTel and HTTP clients.

## Local development

Run a local OpenBox Core. Use `obx_test_*` keys with `OPENBOX_URL=http://localhost:8086` — the localhost host check permits HTTP.
