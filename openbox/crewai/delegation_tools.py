"""DelegateWorkTool / AskQuestionTool subclasses that propagate GovernanceHaltError.

Stock CrewAI BaseAgentTool._execute (base_agent_tools.py:130-134) wraps the
delegated agent's execute_task in a broad `except Exception`. That swallows
GovernanceHaltError, hands a stringified error to the parent's LLM, and lets
the crew/flow continue past a HALT verdict. These subclasses narrow the catch
so HALT propagates while every other failure mode keeps upstream behaviour.

Verified against crewai==1.15.23. When bumping crewai, re-verify:
  - base_agent_tools.py still has `except Exception as e:` around execute_task
  - i18n keys 'agent_tool_unexisting_coworker', 'agent_tool_execution_error',
    'manager_request' still exist
  - AgentTools.tools() still returns [DelegateWorkTool, AskQuestionTool] only
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.task import Task
from crewai.tools.agent_tools.ask_question_tool import AskQuestionTool
from crewai.tools.agent_tools.base_agent_tools import BaseAgentTool
from crewai.tools.agent_tools.delegate_work_tool import DelegateWorkTool
from crewai.tools.base_tool import BaseTool
from crewai.utilities.i18n import I18N, get_i18n
from pydantic import Field

from openbox.core.errors import IDENTITY_ERRORS, GovernanceBlockedError, GovernanceHaltError


class _OpenBoxAgentToolMixin:
    """Re-raise governance halt/block errors; fall through to upstream
    string-error behaviour for everything else."""

    i18n: I18N

    def _strict_execute(
        self,
        agent_name: str | None,
        task: str,
        context: str | None,
    ) -> str:
        tool = cast(BaseAgentTool, self)
        sanitized = tool.sanitize_agent_name(agent_name or "")
        match = [
            a
            for a in tool.agents
            if tool.sanitize_agent_name(a.role) == sanitized
        ]
        if not match:
            return self.i18n.errors("agent_tool_unexisting_coworker").format(
                coworkers="\n".join(
                    f"- {tool.sanitize_agent_name(a.role)}"
                    for a in tool.agents
                ),
                error=f"No agent found with role '{sanitized}'",
            )
        selected = match[0]
        delegated = Task(
            description=task,
            agent=selected,
            expected_output=self.i18n.slice("manager_request"),
        )
        try:
            return selected.execute_task(delegated, context)
        except (*IDENTITY_ERRORS, GovernanceHaltError, GovernanceBlockedError):
            raise
        except Exception as e:
            return self.i18n.errors("agent_tool_execution_error").format(
                agent_role=tool.sanitize_agent_name(selected.role),
                error=str(e),
            )


class OpenBoxDelegateWorkTool(_OpenBoxAgentToolMixin, DelegateWorkTool):
    # CrewAI removed this field; retain the OpenBox factory's custom prompts.
    i18n: I18N = Field(default_factory=get_i18n, exclude=True)

    def _run(
        self,
        task: str,
        context: str,
        coworker: str | None = None,
        **kwargs: Any,
    ) -> str:
        return self._strict_execute(
            self._get_coworker(coworker, **kwargs), task, context,
        )


class OpenBoxAskQuestionTool(_OpenBoxAgentToolMixin, AskQuestionTool):
    i18n: I18N = Field(default_factory=get_i18n, exclude=True)

    def _run(
        self,
        question: str,
        context: str,
        coworker: str | None = None,
        **kwargs: Any,
    ) -> str:
        return self._strict_execute(
            self._get_coworker(coworker, **kwargs), question, context,
        )


def make_openbox_delegation_tools(
    agents: Sequence[BaseAgent],
    i18n: I18N | None = None,
) -> list[BaseTool]:
    """Build the OpenBox delegation-tool pair.

    Mirrors crewai.tools.agent_tools.AgentTools.tools() so the LLM-facing tool
    descriptions match upstream prompt engineering.
    """
    resolved = i18n if i18n is not None else get_i18n()
    coworkers = ", ".join(a.role for a in agents)
    agent_list = list(agents)
    tools: list[BaseTool] = [
        OpenBoxDelegateWorkTool(
            agents=agent_list,
            i18n=resolved,
            description=cast(str, resolved.tools("delegate_work")).format(
                coworkers=coworkers
            ),
        ),
        OpenBoxAskQuestionTool(
            agents=agent_list,
            i18n=resolved,
            description=cast(str, resolved.tools("ask_question")).format(
                coworkers=coworkers
            ),
        ),
    ]
    return tools


def openbox_manager_get_delegation_tools(
    self: BaseAgent, agents: Sequence[BaseAgent],
) -> list[BaseTool]:
    """Bound to a vanilla manager_agent by GovernedCrew to keep HALT propagation.

    Module-level so GovernedCrew can identity-check it for idempotency.
    """
    return make_openbox_delegation_tools(agents)
