"""Delegation tools must re-raise GovernanceHaltError, not stringify it."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest
from crewai import Agent

from openbox.core.errors import GovernanceHaltError


def _make_real_agent(role: str = "Worker") -> Agent:
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fake-key-for-tests"}):
        return Agent(role=role, goal="g", backstory="b", allow_delegation=False)


def _build_mixin(coworker_role: str, raise_exc: BaseException | None):
    from openbox.crewai.delegation_tools import OpenBoxDelegateWorkTool

    agent = _make_real_agent(coworker_role)

    def _execute(_task: Any, _context: Any = None) -> str:
        if raise_exc is not None:
            raise raise_exc
        return "ok"

    object.__setattr__(agent, "execute_task", _execute)
    return OpenBoxDelegateWorkTool(agents=[agent], description="delegate to a coworker")


def test_strict_execute_reraises_governance_halt() -> None:
    tool = _build_mixin(
        "Worker",
        GovernanceHaltError("halted", verdict="halt", reason="x"),
    )
    with pytest.raises(GovernanceHaltError):
        tool._strict_execute("Worker", "task", "ctx")


def test_strict_execute_other_exceptions_become_string_errors() -> None:
    tool = _build_mixin("Worker", RuntimeError("boom"))
    out = tool._strict_execute("Worker", "task", "ctx")
    assert isinstance(out, str)
    assert "boom" in out


def test_strict_execute_unknown_coworker_returns_error_string() -> None:
    tool = _build_mixin("Worker", None)
    out = tool._strict_execute("DoesNotExist", "task", "ctx")
    assert isinstance(out, str)
    assert "DoesNotExist" in out or "coworker" in out.lower()


def test_strict_execute_success_passthrough() -> None:
    tool = _build_mixin("Worker", None)
    assert tool._strict_execute("Worker", "task", "ctx") == "ok"


def test_make_openbox_delegation_tools_returns_two_tools() -> None:
    from openbox.crewai.delegation_tools import (
        make_openbox_delegation_tools,
    )

    tools = make_openbox_delegation_tools([_make_real_agent()])
    assert len(tools) == 2
    types = {type(t).__name__ for t in tools}
    assert "OpenBoxDelegateWorkTool" in types
    assert "OpenBoxAskQuestionTool" in types


def test_openbox_agent_returns_openbox_delegation_tools() -> None:
    from openbox.crewai.delegation_tools import OpenBoxDelegateWorkTool
    from tests.unit.conftest import make_agent

    agent = make_agent()
    tools = agent.get_delegation_tools([_make_real_agent()])
    assert any(isinstance(t, OpenBoxDelegateWorkTool) for t in tools)
