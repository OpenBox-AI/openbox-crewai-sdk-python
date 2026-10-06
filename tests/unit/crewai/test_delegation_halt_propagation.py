"""Delegation tools must re-raise GovernanceHaltError, not stringify it."""

from __future__ import annotations

import json
import os
from pathlib import Path
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


def test_delegation_tools_preserve_custom_prompts(tmp_path: Path) -> None:
    from crewai.utilities.i18n import I18N, get_i18n

    from openbox.crewai.delegation_tools import make_openbox_delegation_tools

    prompts = json.loads(json.dumps(get_i18n()._prompts))
    prompts["errors"]["agent_tool_unexisting_coworker"] = "Missing: {coworkers}"
    prompts["errors"]["agent_tool_execution_error"] = "Failed: {error}"
    prompts["slices"]["manager_request"] = "Custom expected output"
    prompt_file = tmp_path / "prompts.json"
    prompt_file.write_text(json.dumps(prompts))
    i18n = I18N(prompt_file=str(prompt_file))
    agent = _make_real_agent()
    delegated_tasks = []

    def fail(task: Any, _context: Any = None) -> str:
        delegated_tasks.append(task)
        raise RuntimeError("boom")

    object.__setattr__(agent, "execute_task", fail)
    for tool in make_openbox_delegation_tools([agent], i18n=i18n):
        assert tool._run("task", "context", coworker="unknown") == "Missing: - worker"
        assert tool._run("task", "context", coworker="Worker") == "Failed: boom"

    assert len(delegated_tasks) == 2
    assert all(task.expected_output == "Custom expected output" for task in delegated_tasks)
