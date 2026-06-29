"""BLOCK and HALT must produce distinct SDK errors.

The original implementation collapsed both verdicts onto GovernanceHaltError
and set the per-agent halt flags (_halted, _llm_allowed_var) for both. That
caused two user-visible problems on BLOCK:

  1. _llm_allowed_var=False made subsequent LLM calls trip CrewAI's
     before_llm_call hook, which raised ValueError("LLM call blocked by
     before_llm_call hook"). ValueError isn't a passthrough exception,
     so CrewAI caught it and re-surfaced it as
     "Failed to connect to OpenAI API: Connection error." — burying the
     real BLOCK reason under what looks like a network outage.

  2. The BLOCK could not be distinguished from a HALT by exception type,
     so user code couldn't decide whether to retry / fall back.

Correct semantics: BLOCK is per-call ("deny this specific action"), HALT
is per-agent ("this agent is done"). BLOCK should raise
GovernanceBlockedError without touching _halted or _llm_allowed_var. HALT
should raise GovernanceHaltError and set both flags.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from openbox.core.errors import GovernanceBlockedError, GovernanceHaltError
from openbox.core.types import GovernanceResponse, Verdict
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import _llm_allowed_var

from ..conftest import (
    API_KEY,
    CREW_EXEC_ID,
    CREW_NAME,
    make_agent,
    make_client_mock,
    make_config,
)


def _agent_with_governance():
    client = make_client_mock()
    config = make_config()
    sp = GovernanceSpanProcessor(client, config)
    agent = make_agent()
    with patch.dict(os.environ, {"AGENT_API_KEY": API_KEY}):
        agent.configure_governance(client, sp, config, CREW_NAME, CREW_EXEC_ID)
    return agent, client


@pytest.fixture(autouse=True)
def _reset_llm_allowed():
    _llm_allowed_var.set(True)
    yield
    _llm_allowed_var.set(True)


class TestEvaluateTaskStartedVerdictDistinction:
    def test_block_raises_GovernanceBlockedError(self) -> None:
        agent, client = _agent_with_governance()
        client.evaluate.return_value = GovernanceResponse(
            verdict=Verdict.BLOCK,
            reason="policy rule X blocked this",
            policy_id="rule-X",
        )
        with pytest.raises(GovernanceBlockedError) as exc_info:
            agent._evaluate_task_started(
                client, agent._config, "t", "policy_test", "act-1", [], {}
            )
        assert "policy rule X blocked this" in str(exc_info.value)
        assert exc_info.value.reason == "policy rule X blocked this"
        assert exc_info.value.policy_id == "rule-X"

    def test_block_does_not_set_halted_flag(self) -> None:
        agent, client = _agent_with_governance()
        client.evaluate.return_value = GovernanceResponse(
            verdict=Verdict.BLOCK, reason="blocked"
        )
        with pytest.raises(GovernanceBlockedError):
            agent._evaluate_task_started(
                client, agent._config, "t", "policy_test", "act-1", [], {}
            )
        assert agent._halted is False, "BLOCK is per-call; agent must not be halted"

    def test_block_does_not_disable_llm_hook(self) -> None:
        agent, client = _agent_with_governance()
        client.evaluate.return_value = GovernanceResponse(
            verdict=Verdict.BLOCK, reason="blocked"
        )
        with pytest.raises(GovernanceBlockedError):
            agent._evaluate_task_started(
                client, agent._config, "t", "policy_test", "act-1", [], {}
            )
        assert _llm_allowed_var.get() is True, (
            "BLOCK is per-call; _llm_allowed_var must stay True so subsequent "
            "LLM calls aren't silently turned into ValueError->Connection error"
        )

    def test_halt_raises_GovernanceHaltError(self) -> None:
        agent, client = _agent_with_governance()
        client.evaluate.return_value = GovernanceResponse(
            verdict=Verdict.HALT, reason="agent halted", policy_id="halt-rule"
        )
        with pytest.raises(GovernanceHaltError) as exc_info:
            agent._evaluate_task_started(
                client, agent._config, "t", "policy_test", "act-1", [], {}
            )
        assert "agent halted" in str(exc_info.value)
        assert exc_info.value.reason == "agent halted"

    def test_halt_sets_halted_flag_and_disables_llm_hook(self) -> None:
        agent, client = _agent_with_governance()
        client.evaluate.return_value = GovernanceResponse(
            verdict=Verdict.HALT, reason="halt"
        )
        with pytest.raises(GovernanceHaltError):
            agent._evaluate_task_started(
                client, agent._config, "t", "policy_test", "act-1", [], {}
            )
        assert agent._halted is True
        assert _llm_allowed_var.get() is False


class TestEvaluateTaskCompletedVerdictDistinction:
    def test_block_raises_GovernanceBlockedError(self) -> None:
        agent, client = _agent_with_governance()
        client.evaluate.return_value = GovernanceResponse(
            verdict=Verdict.BLOCK, reason="output blocked", policy_id="output-rule"
        )
        with pytest.raises(GovernanceBlockedError) as exc_info:
            agent._evaluate_task_completed(
                client, agent._config, "t", "policy_test", "act-1", "result", {}
            )
        assert "output blocked" in str(exc_info.value)

    def test_block_does_not_halt_on_completed_stage(self) -> None:
        agent, client = _agent_with_governance()
        client.evaluate.return_value = GovernanceResponse(
            verdict=Verdict.BLOCK, reason="output blocked"
        )
        with pytest.raises(GovernanceBlockedError):
            agent._evaluate_task_completed(
                client, agent._config, "t", "policy_test", "act-1", "result", {}
            )
        assert agent._halted is False
        assert _llm_allowed_var.get() is True

    def test_halt_raises_GovernanceHaltError(self) -> None:
        agent, client = _agent_with_governance()
        client.evaluate.return_value = GovernanceResponse(
            verdict=Verdict.HALT, reason="halt on output"
        )
        with pytest.raises(GovernanceHaltError):
            agent._evaluate_task_completed(
                client, agent._config, "t", "policy_test", "act-1", "result", {}
            )
        assert agent._halted is True
        assert _llm_allowed_var.get() is False


class TestDelegationToolPropagatesBlock:
    """The hierarchical manager's delegation tool must re-raise BOTH
    GovernanceHaltError and GovernanceBlockedError. Without this, a
    BLOCK propagating from a worker gets stringified by `except Exception`
    and the manager keeps going as if nothing happened."""

    def _build_tool(self, raise_exc: BaseException | None):
        from crewai import Agent

        from openbox.crewai.delegation_tools import OpenBoxDelegateWorkTool

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fake-key-for-tests"}):
            agent = Agent(role="Worker", goal="g", backstory="b", allow_delegation=False)

        def _execute(_task, _context=None):  # type: ignore[no-untyped-def]
            if raise_exc is not None:
                raise raise_exc
            return "ok"

        object.__setattr__(agent, "execute_task", _execute)
        return OpenBoxDelegateWorkTool(agents=[agent], description="delegate")

    def test_reraises_GovernanceBlockedError(self) -> None:
        tool = self._build_tool(
            GovernanceBlockedError("policy denied", verdict="block", reason="rule X")
        )
        with pytest.raises(GovernanceBlockedError):
            tool._strict_execute("Worker", "task", "ctx")

    def test_still_reraises_GovernanceHaltError(self) -> None:
        # Regression guard — existing behaviour must survive.
        tool = self._build_tool(
            GovernanceHaltError("halted", verdict="halt", reason="x")
        )
        with pytest.raises(GovernanceHaltError):
            tool._strict_execute("Worker", "task", "ctx")
