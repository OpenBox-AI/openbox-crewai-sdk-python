"""Run-scoped state on OpenBoxAgent must reset between kickoffs."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from openbox.core.errors import GovernanceHaltError
from openbox.crewai.crew import GovernedCrew
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import _llm_allowed_var

from ..conftest import API_KEY, CREW_NAME, make_agent, make_client_mock, make_config


class TestResetRunState:
    def test_clears_run_scoped_fields(self) -> None:
        agent = make_agent()
        agent._session_id = "stale-session"
        agent._run_id = "stale-run"
        agent._session_started = True
        agent._halted = True

        agent._reset_run_state()

        assert agent._session_id is None
        assert agent._run_id is None
        assert agent._session_started is False
        assert agent._halted is False

    def test_preserves_persistent_config(self) -> None:
        agent = make_agent()
        agent._openbox_api_key = "preserved-key"
        agent._crew_name = "preserved-crew"
        agent._crew_execution_id = "preserved-exec"

        agent._reset_run_state()

        assert agent._openbox_api_key == "preserved-key"
        assert agent._crew_name == "preserved-crew"
        assert agent._crew_execution_id == "preserved-exec"


class TestKickoffResetsAgentState:
    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_halt_in_prior_run_does_not_block_next_run(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        agent._halted = True
        agent._session_started = True

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "kickoff", return_value="done"),
        ):
            crew.kickoff(engine=object())

        assert agent._halted is False
        assert agent._session_started is False

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_cleanup_resets_state_after_run(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        def _simulate_run(*_args, **_kwargs):
            agent._session_started = True
            agent._session_id = "sess-during-run"
            agent._run_id = "run-during-run"
            agent._halted = True
            return "done"

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "kickoff", side_effect=_simulate_run),
        ):
            crew.kickoff(engine=object())

        assert agent._session_id is None
        assert agent._run_id is None
        assert agent._session_started is False
        assert agent._halted is False

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_cleanup_resets_state_when_close_session_raises(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        def _simulate_run(*_args, **_kwargs):
            agent._session_started = True
            agent._session_id = "sess"
            agent._run_id = "run"
            return "done"

        client.evaluate.side_effect = [
            client.evaluate.return_value,
            RuntimeError("close failed"),
        ]

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "kickoff", side_effect=_simulate_run),
        ):
            crew.kickoff(engine=object())

        assert agent._session_started is False
        assert agent._halted is False

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_kickoff_resets_stale_llm_gate_before_run(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        token = _llm_allowed_var.set(False)
        try:
            with (
                patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
                patch.object(GovernedCrew.__bases__[0], "kickoff", return_value="done"),
            ):
                crew.kickoff(engine=object())
            assert _llm_allowed_var.get() is True
        finally:
            _llm_allowed_var.reset(token)


class TestAcleanupResetsAgentState:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    async def test_acleanup_resets_state_after_run(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        async def _fake_akickoff(*_args, **_kwargs):
            agent._session_started = True
            agent._session_id = "sess"
            agent._run_id = "run"
            agent._halted = True
            return "done"

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "akickoff", side_effect=_fake_akickoff),
        ):
            await crew.akickoff(engine=object())

        assert agent._session_id is None
        assert agent._run_id is None
        assert agent._session_started is False
        assert agent._halted is False

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    async def test_akickoff_resets_stale_llm_gate_before_run(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        token = _llm_allowed_var.set(False)
        try:
            with (
                patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
                patch.object(GovernedCrew.__bases__[0], "akickoff", return_value="done"),
            ):
                await crew.akickoff(engine=object())
            assert _llm_allowed_var.get() is True
        finally:
            _llm_allowed_var.reset(token)


class TestKickoffDrainsLeftoverSessions:
    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_leftover_session_closed_at_kickoff_start(self) -> None:
        """A prior crashed run's open session is closed before the new run begins."""
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "kickoff", return_value="done"),
        ):
            crew.kickoff(engine=object())
            agent._session_started = True
            agent._session_id = "leftover-sess"
            agent._run_id = "leftover-run"
            agent._halted = False

            client.evaluate.reset_mock()
            crew.kickoff(engine=object())

        completed_calls = [
            c
            for c in client.evaluate.call_args_list
            if c[0][0].get("event_type") == "WorkflowCompleted"
        ]
        assert any(c[0][0]["status"] == "completed" for c in completed_calls), (
            "drain should emit WorkflowCompleted for the leftover session"
        )
        assert agent._session_started is False
        assert agent._session_id is None

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_leftover_halted_session_closed_as_halted(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "kickoff", return_value="done"),
        ):
            crew.kickoff(engine=object())
            agent._session_started = True
            agent._session_id = "leftover-sess"
            agent._run_id = "leftover-run"
            agent._halted = True

            client.evaluate.reset_mock()
            crew.kickoff(engine=object())

        completed_calls = [
            c
            for c in client.evaluate.call_args_list
            if c[0][0].get("event_type") == "WorkflowCompleted"
        ]
        assert any(c[0][0]["status"] == "halted" for c in completed_calls), (
            "drain should emit WorkflowCompleted with status=halted for a halted leftover"
        )

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_drain_failure_does_not_block_new_run(self) -> None:
        """If draining the leftover raises, the new run still proceeds with clean state."""
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "kickoff", return_value="done"),
        ):
            crew.kickoff(engine=object())
            agent._session_started = True
            agent._session_id = "leftover-sess"
            agent._run_id = "leftover-run"

            client.evaluate.side_effect = RuntimeError("drain close failed")
            crew.kickoff(engine=object())

        assert agent._session_started is False
        assert agent._session_id is None
        assert agent._halted is False


class TestExecuteTaskAfterReset:
    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_execute_task_no_longer_raises_after_reset(self) -> None:
        agent = make_agent()
        agent._halted = True

        from openbox.crewai.task import OpenBoxTask

        task = OpenBoxTask(
            description="x",
            expected_output="y",
            agent=agent,
            activity_type="task",
        )

        with pytest.raises(GovernanceHaltError):
            agent.execute_task(task)

        agent._reset_run_state()

        with patch("crewai.Agent.execute_task", return_value="ok"):
            assert agent.execute_task(task) == "ok"
