# Demo 04 — Collaboration

Three governed, signed agents with `allow_delegation=True`.

The Content Writer owns the task and delegates research to the Research Specialist and editorial review to the Content Editor. Every `execute_task` call — including delegated sub-tasks routed through CrewAI's `DelegateWorkTool` — is intercepted by OpenBox governance and signed with its agent's DID.

## Layout

Standard `crewai create crew` layout:

- `src/demo_04_collaboration/crew.py` — `CollaborationCrew` with three `OpenBoxAgent` definitions (`researcher`, `writer`, `editor`) and one `OpenBoxTask` owned by the writer.
- `src/demo_04_collaboration/main.py` — kicks off the governed crew.
- `src/demo_04_collaboration/config/{agents,tasks}.yaml` — agent and task definitions.

## Prerequisites

1. In the OpenBox Platform UI, create three agents — `researcher`, `writer`, and `editor` — and provision an identity for each (**Agent Settings → API Access → Provision Identity**). For each, capture the API key, DID, and base64 private key.
2. Copy `.env.example` to `.env` and fill in the three sets of credentials plus your `OPENAI_API_KEY`.

## Run

```bash
uv sync
crewai run
```

Or directly:

```bash
uv run demo_04_collaboration
```
