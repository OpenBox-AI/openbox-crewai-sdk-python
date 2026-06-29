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
export OPENBOX_RESEARCHER_DID="did:aip:<uuid>"              # enable AIP signing
export OPENBOX_RESEARCHER_PRIVATE_KEY="base64_ed25519_seed" # paired with DID
```

Validation at `engine.govern(crew)`:

- API key matches `obx_live_*` or `obx_test_*`
- non-localhost URLs use HTTPS
- DID and private key configured together when AIP signing is enabled
- API key round-tripped against `/api/v1/auth/validate`

A bad value raises `OpenBoxConfigError`, `OpenBoxAuthError`, or `OpenBoxNetworkError`.

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
