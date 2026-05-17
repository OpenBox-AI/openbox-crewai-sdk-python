# Demo 01 — Crew Sync

Simplest OpenBox demo. One signed agent (`researcher`) runs a single task. The crew is wrapped with `engine.govern(...)` so every `ActivityStarted` / `ActivityCompleted` / operation event is signed with the agent's DID before being sent to OpenBox.

## Layout

This demo follows the standard `crewai create crew` layout, with OpenBox-specific changes:

- `src/demo_01_crew_sync/crew.py` — `ResearchCrew` class. Uses `OpenBoxAgent` and `OpenBoxTask` instead of the plain `Agent` / `Task` classes.
- `src/demo_01_crew_sync/main.py` — entry point. Wraps the crew in an `OpenBoxEngine` context.
- `src/demo_01_crew_sync/config/{agents,tasks}.yaml` — agent and task definitions.

## Prerequisites

1. In the OpenBox Platform UI, create the `researcher` agent and provision its identity (**Agent Settings → API Access → Provision Identity**). Capture the agent's API key, DID, and base64 private key.
2. Copy `.env.example` to `.env` and fill in those values plus your `OPENAI_API_KEY`.

## Run

```bash
uv sync
crewai run
```

Or directly:

```bash
uv run demo_01_crew_sync
```
