"""Halted agents should skip close_session and log refusal."""

from __future__ import annotations

import logging

import pytest

from openbox.core.errors import GovernanceHaltError
from tests.unit.conftest import make_agent, make_client_mock


def _make_started_agent(env_prefix: str = "AGENT") -> object:
    agent = make_agent(env_prefix=env_prefix)
    agent._openbox_api_key = "obx_test_aaaaaaaaaaaaaaaaaaaaaaaa"
    agent._session_id = "session-1"
    agent._run_id = "run-1"
    agent._session_started = True
    return agent


def test_close_session_skipped_when_halted() -> None:
    agent = _make_started_agent()
    agent._halted = True
    client = make_client_mock()
    agent.close_session(client, "c", {"crew_name": "c", "crew_execution_id": "x"})
    assert client.evaluate.call_count == 0


@pytest.mark.asyncio
async def test_aclose_session_skipped_when_halted() -> None:
    agent = _make_started_agent()
    agent._halted = True
    client = make_client_mock()
    await agent._aclose_session(client, "c", {"crew_name": "c", "crew_execution_id": "x"})
    assert client.aevaluate.call_count == 0


def test_close_session_runs_normally_when_not_halted() -> None:
    agent = _make_started_agent()
    agent._halted = False
    client = make_client_mock()
    agent.close_session(client, "c", {"crew_name": "c", "crew_execution_id": "x"})
    assert client.evaluate.call_count == 1


def test_close_session_force_overrides_halt_skip() -> None:
    agent = _make_started_agent()
    agent._halted = True
    client = make_client_mock()
    agent.close_session(
        client,
        "c",
        {"crew_name": "c", "crew_execution_id": "x"},
        status="halted",
        force=True,
    )
    assert client.evaluate.call_count == 1


def test_execute_task_on_halted_agent_logs_warning_and_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from openbox.crewai.task import OpenBoxTask

    agent = _make_started_agent()
    agent._halted = True
    task = OpenBoxTask(
        description="t", expected_output="o", agent=agent, activity_type="task"
    )
    with caplog.at_level(logging.WARNING, logger="openbox"):
        with pytest.raises(GovernanceHaltError):
            agent.execute_task(task)
    assert any("halted" in r.message.lower() for r in caplog.records)
