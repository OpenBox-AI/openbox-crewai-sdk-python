"""E2E test — single OpenBoxAgent with a configured DID + private key.

Runs a minimal governed crew (1 agent, 1 task) with Agent Identity Protocol
signing disabled.

Prerequisites (in this folder's .env):
    OPENBOX_URL                             Core base URL
    OPENAI_API_KEY / OPENAI_MODEL_NAME      LLM for CrewAI
    OPENBOX_NO_DID_TESTER_API_KEY           API for a provisioned agent

Usage:
    uv run python3 -m pytest tests/e2e/did/ -v
"""

import logging
import os
from pathlib import Path

import pytest
from crewai import LLM, Crew, Process
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

load_dotenv(Path(__file__).parent / ".env")

ENV_PREFIX = "OPENBOX_NO_DID_TESTER"


def _make_agent() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="DID Signing Tester",
        goal="Execute the task exactly as described.",
        backstory="A test agent that signs every governance request with its DID.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
        env_prefix=ENV_PREFIX,
    )


def test_signed_single_agent_no_did_crew(caplog: pytest.LogCaptureFixture) -> None:
    agent = _make_agent()

    task = OpenBoxTask(
        name="did_signed_allow",
        activity_type="did_test",
        description="List 3 benefits of cloud computing.",
        expected_output="A numbered list of 3 benefits.",
        agent=agent,
    )

    with create_openbox_engine(
        governance_policy="fail_closed",
        on_fallback="fail_closed",
        debug_log=True,
    ) as engine:
        crew = Crew(
            name="did-single-agent-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        with caplog.at_level(logging.WARNING, logger="openbox"):
            governed_crew = engine.govern(crew)
            result = governed_crew.kickoff()

        assert result is not None

    governance_warnings = [
        r for r in caplog.records if r.name.startswith("openbox") and r.levelno >= logging.WARNING
    ]
    assert not governance_warnings, (
        "Core returned errors or fallback responses during signed evaluation:\n"
        + "\n".join(f"{r.levelname} {r.name}: {r.getMessage()}" for r in governance_warnings)
    )
