"""E2E test — agent with signing_required=false is NOT verified even with a bad key.

Proves the `signing_required=false` gate actually disables signature verification
on Core. The SDK is configured to sign every governance request with a freshly
generated (wrong) Ed25519 seed, but the request is sent under an API key for an
agent whose `signing_required` is false. If Core were still verifying, the 401
rejection from test_single_agent_bad_key.py would fire here too. It must not.

Expected outcome: crew.kickoff() completes with no governance warnings — the
bad signature is accepted because Core never checked it.

Prerequisites (in this folder's .env):
    OPENBOX_URL                                 Core base URL
    OPENAI_API_KEY / OPENAI_MODEL_NAME          LLM for CrewAI
    OPENBOX_NO_SIGNING_TESTER_API_KEY           API key for an agent with signing_required=false
    OPENBOX_NO_SIGNING_TESTER_DID               any syntactically valid did:aip:<uuid>
    OPENBOX_NO_SIGNING_TESTER_PRIVATE_KEY       placeholder; overwritten with a wrong seed

Usage:
    uv run python3 -m pytest tests/e2e/did/test_single_agent_no_signing_required.py -v
"""

import logging
import os
from pathlib import Path

import pytest
from crewai import LLM, Crew, Process
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

from ._helpers import generate_wrong_seed_b64

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger(__name__)

ENV_PREFIX = "OPENBOX_NO_SIGNING_TESTER"
API_KEY_ENV = f"{ENV_PREFIX}_API_KEY"
DID_ENV = f"{ENV_PREFIX}_DID"
PRIVATE_KEY_ENV = f"{ENV_PREFIX}_PRIVATE_KEY"


def _make_agent() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="DID Signing Tester",
        goal="Execute the task exactly as described.",
        backstory="A test agent that signs every governance request with a bogus key.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
        env_prefix=ENV_PREFIX,
    )


def test_signing_off_accepts_bad_key(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert os.environ.get(API_KEY_ENV), (
        f"{API_KEY_ENV} must be set and resolve to an agent with signing_required=false"
    )
    assert os.environ.get(DID_ENV), f"{DID_ENV} must be set to any valid did:aip:<uuid>"

    wrong_seed = generate_wrong_seed_b64()
    monkeypatch.setenv(PRIVATE_KEY_ENV, wrong_seed)

    agent = _make_agent()

    task = OpenBoxTask(
        name="signing_off_bad_key_allow",
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
            name="did-single-agent-signing-off-bad-key-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        with caplog.at_level(logging.WARNING, logger="openbox"):
            governed_crew = engine.govern(crew)
            result = governed_crew.kickoff()

        assert result is not None, (
            "crew.kickoff() must return a result — if it raised, Core is still verifying "
            "signatures for signing_required=false agents"
        )

    governance_warnings = [
        r for r in caplog.records if r.name.startswith("openbox") and r.levelno >= logging.WARNING
    ]
    assert not governance_warnings, (
        "Core emitted warnings — signing_required=false should bypass verification entirely:\n"
        + "\n".join(f"{r.levelname} {r.name}: {r.getMessage()}" for r in governance_warnings)
    )

    log.info("bad signature accepted as expected — signing_required=false gate confirmed")
