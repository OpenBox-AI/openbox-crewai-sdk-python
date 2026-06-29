"""Verify async governance paths: aexecute_task, akickoff, async client methods."""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openbox.core.errors import GovernanceBlockedError, GovernanceHaltError
from openbox.core.types import GovernanceResponse, GuardrailsResult, Verdict
from openbox.crewai.crew import GovernedCrew
from openbox.crewai.task import OpenBoxTask
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import _multi_agent_session_id_var

from ..conftest import (
    API_KEY,
    CREW_EXEC_ID,
    CREW_NAME,
    MULTI_AGENT_SESSION_ID,
    make_agent,
    make_client_mock,
    make_config,
)


class TestAsyncAgentPayloads:
    @pytest.fixture(autouse=True)
    def setup_agent(self) -> None:
        self.client = make_client_mock()
        self.config = make_config()
        self.sp = GovernanceSpanProcessor(self.client, self.config)
        self.agent = make_agent()
        with patch.dict(os.environ, {"AGENT_API_KEY": API_KEY}):
            self.agent.configure_governance(
                self.client, self.sp, self.config, CREW_NAME, CREW_EXEC_ID
            )

    def _get_last_async_payload(self) -> dict[str, Any]:
        return self.client.aevaluate.call_args[0][0]

    # -- _aensure_session (WorkflowStarted) -----------------------------------

    async def test_aensure_session_sends_workflow_started(self) -> None:
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        await self.agent._aensure_session(self.client, CREW_NAME, metadata)
        payload = self._get_last_async_payload()
        assert payload["event_type"] == "WorkflowStarted"
        assert payload["metadata"]["crew_name"] == CREW_NAME
        assert payload["task_queue"] == CREW_NAME

    async def test_aensure_session_idempotent(self) -> None:
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        await self.agent._aensure_session(self.client, CREW_NAME, metadata)
        await self.agent._aensure_session(self.client, CREW_NAME, metadata)
        assert self.client.aevaluate.call_count == 1

    # -- _aclose_session (WorkflowCompleted) ----------------------------------

    async def test_aclose_session_sends_workflow_completed(self) -> None:
        metadata = {
            "crew_name": CREW_NAME,
            "crew_execution_id": CREW_EXEC_ID,
            "multi_agent_session_id": MULTI_AGENT_SESSION_ID,
        }
        await self.agent._aensure_session(self.client, CREW_NAME, metadata)
        await self.agent._aclose_session(self.client, CREW_NAME, metadata)
        payload = self._get_last_async_payload()
        assert payload["event_type"] == "WorkflowCompleted"
        assert payload["metadata"]["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID

    # -- _aevaluate_task_started (ActivityStarted) ----------------------------

    async def test_aevaluate_task_started_has_all_metadata(self) -> None:
        metadata = {
            "crew_name": CREW_NAME,
            "crew_execution_id": CREW_EXEC_ID,
            "multi_agent_session_id": MULTI_AGENT_SESSION_ID,
        }
        await self.agent._aevaluate_task_started(
            self.client, self.config, "research_task", "policy_test", "act-001", [], metadata
        )
        payload = self._get_last_async_payload()
        assert payload["event_type"] == "ActivityStarted"
        assert payload["metadata"]["crew_name"] == CREW_NAME
        assert payload["metadata"]["crew_execution_id"] == CREW_EXEC_ID
        assert payload["metadata"]["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID

    async def test_aevaluate_task_started_block_raises(self) -> None:
        self.client.aevaluate = AsyncMock(
            return_value=GovernanceResponse(verdict=Verdict.BLOCK, reason="blocked")
        )
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        with pytest.raises(GovernanceBlockedError):
            await self.agent._aevaluate_task_started(
                self.client, self.config, "task", "type", "act-001", [], metadata
            )
        assert self.agent._halted is False

    async def test_aevaluate_task_started_approval_polls(self) -> None:
        self.client.aevaluate = AsyncMock(
            return_value=GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL)
        )
        self.client.await_for_approval = AsyncMock(
            return_value=GovernanceResponse(verdict=Verdict.ALLOW, reason="approved")
        )
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        response = await self.agent._aevaluate_task_started(
            self.client, self.config, "task", "type", "act-001", [], metadata
        )
        assert response.verdict == Verdict.ALLOW
        self.client.await_for_approval.assert_awaited_once()

    # -- _aevaluate_task_completed (ActivityCompleted) ------------------------

    async def test_aevaluate_task_completed_has_all_metadata(self) -> None:
        metadata = {
            "crew_name": CREW_NAME,
            "crew_execution_id": CREW_EXEC_ID,
            "multi_agent_session_id": MULTI_AGENT_SESSION_ID,
        }
        await self.agent._aevaluate_task_completed(
            self.client, self.config, "task", "type", "act-001", "result", metadata
        )
        activity_completed = next(
            call.args[0]
            for call in self.client.aevaluate.call_args_list
            if call.args and call.args[0].get("event_type") == "ActivityCompleted"
        )
        assert activity_completed["metadata"]["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID

    async def test_aevaluate_task_completed_redacts_output(self) -> None:
        self.client.aevaluate = AsyncMock(
            return_value=GovernanceResponse(
                verdict=Verdict.ALLOW,
                guardrails_result=GuardrailsResult(
                    redacted_input={"result": "REDACTED"},
                    input_type="activity_output",
                ),
            )
        )
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        result = await self.agent._aevaluate_task_completed(
            self.client, self.config, "task", "type", "act-001", "sensitive data", metadata
        )
        assert result == "REDACTED"

    async def test_aevaluate_task_completed_block_raises(self) -> None:
        self.client.aevaluate = AsyncMock(
            return_value=GovernanceResponse(verdict=Verdict.HALT, reason="halted")
        )
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        with pytest.raises(GovernanceHaltError):
            await self.agent._aevaluate_task_completed(
                self.client, self.config, "task", "type", "act-001", "result", metadata
            )
        assert self.agent._halted is True


class TestAsyncExecuteTask:
    @pytest.fixture(autouse=True)
    def setup_agent(self) -> None:
        self.client = make_client_mock()
        self.config = make_config()
        self.sp = GovernanceSpanProcessor(self.client, self.config)
        self.agent = make_agent()
        with patch.dict(os.environ, {"AGENT_API_KEY": API_KEY}):
            self.agent.configure_governance(
                self.client, self.sp, self.config, CREW_NAME, CREW_EXEC_ID
            )

    async def test_aexecute_task_coerces_plain_task(self) -> None:
        """A plain crewai.Task (e.g. from delegate_work_to_coworker) is coerced
        to OpenBoxTask with activity_type='delegation' so governance still
        fires for delegated work."""
        from crewai import Task

        task = Task(description="plain task", expected_output="output")
        with patch.object(
            type(self.agent).__bases__[0], "aexecute_task", new_callable=AsyncMock
        ) as mock_super:
            mock_super.return_value = "result"
            await self.agent.aexecute_task(task)

        # WorkflowStarted + user_input + ActivityStarted + ActivityCompleted + agent_output.
        assert self.client.aevaluate.call_count == 5
        activity_started = next(
            c[0][0]
            for c in self.client.aevaluate.call_args_list
            if c[0][0].get("event_type") == "ActivityStarted"
        )
        assert activity_started["activity_type"] == "delegation"

    async def test_aexecute_task_halted_agent_raises(self) -> None:
        task = OpenBoxTask(
            description="test", expected_output="output", activity_type="policy_test"
        )
        self.agent._halted = True
        with pytest.raises(GovernanceHaltError):
            await self.agent.aexecute_task(task)

    async def test_aexecute_task_calls_governance(self) -> None:
        task = OpenBoxTask(
            description="test", expected_output="output", activity_type="policy_test"
        )
        with patch.object(
            type(self.agent).__bases__[0], "aexecute_task", new_callable=AsyncMock
        ) as mock_super:
            mock_super.return_value = "done"
            result = await self.agent.aexecute_task(task)

        assert result == "done"
        # WorkflowStarted + user_input + ActivityStarted + ActivityCompleted + agent_output.
        assert self.client.aevaluate.call_count == 5


class TestAsyncCrewKickoff:
    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    async def test_akickoff_configures_agents(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(
                GovernedCrew.__bases__[0], "akickoff", new_callable=AsyncMock, return_value="done"
            ),
        ):
            await crew.akickoff(engine=object())

        assert crew._crew_execution_id
        uuid.UUID(crew._crew_execution_id)
        assert agent._crew_execution_id == crew._crew_execution_id
        assert agent._crew_name == CREW_NAME

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    async def test_akickoff_includes_multi_agent_session_id(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        token = _multi_agent_session_id_var.set(MULTI_AGENT_SESSION_ID)
        try:
            with (
                patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
                patch.object(
                    GovernedCrew.__bases__[0],
                    "akickoff",
                    new_callable=AsyncMock,
                    return_value="done",
                ),
            ):
                await crew.akickoff(engine=object())
        finally:
            _multi_agent_session_id_var.reset(token)

        assert crew._crew_execution_id

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    async def test_akickoff_cleanup_calls_aclose_session(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()
        with patch.dict(os.environ, {"AGENT_API_KEY": API_KEY}):
            agent.configure_governance(client, sp, config, CREW_NAME, CREW_EXEC_ID)

        # Simulate a started session
        agent._session_started = True
        agent._session_id = "sess-001"
        agent._run_id = "run-001"

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(
                GovernedCrew.__bases__[0], "akickoff", new_callable=AsyncMock, return_value="done"
            ),
        ):
            await crew.akickoff(engine=object())

        # _aclose_session calls aevaluate with WorkflowCompleted
        found_close = False
        for call in client.aevaluate.call_args_list:
            payload = call[0][0]
            if payload.get("event_type") == "WorkflowCompleted":
                found_close = True
                assert payload["status"] == "completed"
        assert found_close, "Expected WorkflowCompleted payload from _aclose_session"
