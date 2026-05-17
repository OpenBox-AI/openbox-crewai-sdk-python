"""GovernedCrew — CrewAI Crew with governance lifecycle management."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from crewai import Crew
from crewai.hooks.llm_hooks import (
    register_before_llm_call_hook,
    unregister_before_llm_call_hook,
)
from pydantic import PrivateAttr

from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.errors import (
    GovernanceAPIError,
    GovernanceApprovalExpiredError,
    GovernanceBlockedError,
    GovernanceHaltError,
    OpenBoxConfigError,
    raise_governance_block,
)
from openbox.crewai.agent import OpenBoxAgent
from openbox.engine import OpenBoxEngine
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import (
    _llm_allowed_var,
    _llm_block_info_var,
    _multi_agent_session_id_var,
)


def _register_passthrough_exceptions() -> None:
    """Register governance exceptions so CrewAI doesn't retry them."""
    import crewai.agent.core as _core

    governance_exceptions = (
        GovernanceHaltError,
        GovernanceBlockedError,
        GovernanceAPIError,
        GovernanceApprovalExpiredError,
    )
    existing = _core._passthrough_exceptions
    missing = tuple(e for e in governance_exceptions if e not in existing)
    if missing:
        _core._passthrough_exceptions = existing + missing


logger = logging.getLogger("openbox")


class GovernedCrew(Crew):
    _governance_client: GovernanceClient | None = PrivateAttr(default=None)
    _span_processor: GovernanceSpanProcessor | None = PrivateAttr(default=None)
    _config: GovernanceConfig | None = PrivateAttr(default=None)
    _governance_engine: OpenBoxEngine | None = PrivateAttr(default=None)
    _crew_execution_id: str = PrivateAttr(default="")
    _llm_hook: Any = PrivateAttr(default=None)

    def configure_governance(
        self,
        client: GovernanceClient,
        config: GovernanceConfig,
        span_processor: GovernanceSpanProcessor,
        engine: OpenBoxEngine | None = None,
    ) -> None:
        self._governance_client = client
        self._config = config
        self._span_processor = span_processor
        self._governance_engine = engine

    def bind_openbox_engine(self, engine: OpenBoxEngine) -> GovernedCrew:
        governed_agents = self._governed_agents()
        if not governed_agents:
            raise OpenBoxConfigError(
                "At least one agent must be an OpenBoxAgent. Plain Agent instances are not governed."
            )
        engine.validate_agent_api_keys(governed_agents)
        self.configure_governance(
            engine.client,
            engine.config,
            engine.ensure_started(),
            engine=engine,
        )
        return self

    def _governed_agents(self) -> list[OpenBoxAgent]:
        agents: list[OpenBoxAgent] = [a for a in self.agents if isinstance(a, OpenBoxAgent)]
        manager = getattr(self, "manager_agent", None)
        if isinstance(manager, OpenBoxAgent) and manager not in agents:
            agents.append(manager)
        return agents

    def _create_manager_agent(self) -> None:
        """Equip hierarchical-process managers with HALT-propagating delegation tools.

        Stock crewai.Crew._create_manager_agent instantiates AgentTools(...).tools()
        inline for the auto-created manager, bypassing OpenBoxAgent.get_delegation_tools
        and letting HALT raised by a worker get swallowed by the stock DelegateWorkTool.
        """
        from types import MethodType

        from crewai import Agent as CrewAIAgent
        from crewai.utilities.i18n import get_i18n
        from crewai.utilities.llm_utils import create_llm

        from openbox.crewai.delegation_tools import (
            make_openbox_delegation_tools,
            openbox_manager_get_delegation_tools,
        )

        if self.manager_agent is not None:
            super()._create_manager_agent()
            manager = self.manager_agent
            if isinstance(manager, OpenBoxAgent):
                return
            current = manager.get_delegation_tools
            if (
                hasattr(current, "__func__")
                and current.__func__ is openbox_manager_get_delegation_tools
            ):
                return
            if type(manager).get_delegation_tools is not CrewAIAgent.get_delegation_tools:
                logger.warning(
                    "manager_agent (%s) overrides get_delegation_tools; HALT "
                    "propagation through delegation may not work. Use OpenBoxAgent "
                    "or subclass OpenBoxDelegateWorkTool/OpenBoxAskQuestionTool "
                    "in your override.",
                    type(manager).__name__,
                )
                return
            object.__setattr__(
                manager,
                "get_delegation_tools",
                MethodType(openbox_manager_get_delegation_tools, manager),
            )
            return

        self.manager_llm = create_llm(self.manager_llm)
        i18n = get_i18n(prompt_file=self.prompt_file)
        manager = CrewAIAgent(
            role=i18n.retrieve("hierarchical_manager_agent", "role"),
            goal=i18n.retrieve("hierarchical_manager_agent", "goal"),
            backstory=i18n.retrieve("hierarchical_manager_agent", "backstory"),
            tools=make_openbox_delegation_tools(self.agents, i18n=i18n),
            allow_delegation=True,
            llm=self.manager_llm,
            verbose=self.verbose,
        )
        self.manager_agent = manager
        manager.crew = self

    def kickoff(
        self,
        engine: OpenBoxEngine | None = None,
        inputs: dict[str, Any] | None = None,
        input_files: dict[str, Any] | None = None,
    ) -> Any:
        governed_agents = self._governed_agents()
        if engine is not None:
            self.bind_openbox_engine(engine)
        if governed_agents and (not self._governance_client or not self._config):
            raise OpenBoxConfigError(
                "This governed crew has not been bound to an OpenBoxEngine. Use "
                "engine.govern(crew) before kickoff()."
            )
        if not self._governance_client or not self._config:
            return super().kickoff(inputs=inputs, input_files=input_files)

        self._reset_execution_gate()
        self._drain_leftover_sessions()

        self._crew_execution_id = str(uuid.uuid4())
        crew_name = self.name or "crew"
        metadata: dict[str, Any] = {
            "crew_name": crew_name,
            "crew_execution_id": self._crew_execution_id,
        }

        # Outer wins: if a Flow above us already opened a session, propagate.
        # Otherwise this Crew opens its own per-kickoff session.
        session_token = None
        if _multi_agent_session_id_var.get() is None:
            session_token = _multi_agent_session_id_var.set(str(uuid.uuid4()))

        assert self._span_processor is not None
        for agent in self._governed_agents():
            agent.configure_governance(
                self._governance_client,
                self._span_processor,
                self._config,
                crew_name,
                self._crew_execution_id,
                engine=self._governance_engine,
            )

        _register_passthrough_exceptions()

        if self._config.llm_level_governance:
            self._llm_hook = _make_llm_hook()
            register_before_llm_call_hook(self._llm_hook)

        try:
            return super().kickoff(inputs=inputs, input_files=input_files)
        finally:
            try:
                self._cleanup(crew_name, metadata)
            finally:
                self._reset_execution_gate()
                for agent in self._governed_agents():
                    agent._reset_run_state()
                if session_token is not None:
                    _multi_agent_session_id_var.reset(session_token)

    async def akickoff(
        self,
        engine: OpenBoxEngine | None = None,
        inputs: dict[str, Any] | None = None,
        input_files: dict[str, Any] | None = None,
    ) -> Any:
        governed_agents = self._governed_agents()
        if engine is not None:
            self.bind_openbox_engine(engine)
        if governed_agents and (not self._governance_client or not self._config):
            raise OpenBoxConfigError(
                "This governed crew has not been bound to an OpenBoxEngine. Use "
                "engine.govern(crew) before akickoff()."
            )
        if not self._governance_client or not self._config:
            return await super().akickoff(inputs=inputs, input_files=input_files)

        self._reset_execution_gate()
        await self._adrain_leftover_sessions()

        self._crew_execution_id = str(uuid.uuid4())
        crew_name = self.name or "crew"
        metadata: dict[str, Any] = {
            "crew_name": crew_name,
            "crew_execution_id": self._crew_execution_id,
        }

        # Outer wins: if a Flow above us already opened a session, propagate.
        # Otherwise this Crew opens its own per-kickoff session.
        session_token = None
        if _multi_agent_session_id_var.get() is None:
            session_token = _multi_agent_session_id_var.set(str(uuid.uuid4()))

        assert self._span_processor is not None
        for agent in self._governed_agents():
            agent.configure_governance(
                self._governance_client,
                self._span_processor,
                self._config,
                crew_name,
                self._crew_execution_id,
                engine=self._governance_engine,
            )

        _register_passthrough_exceptions()

        if self._config.llm_level_governance:
            self._llm_hook = _make_llm_hook()
            register_before_llm_call_hook(self._llm_hook)

        try:
            return await super().akickoff(inputs=inputs, input_files=input_files)
        finally:
            try:
                await self._acleanup(crew_name, metadata)
            finally:
                self._reset_execution_gate()
                for agent in self._governed_agents():
                    agent._reset_run_state()
                if session_token is not None:
                    _multi_agent_session_id_var.reset(session_token)

    def _reset_execution_gate(self) -> None:
        """Restore execution-scoped LLM gating to the default allow state."""
        _llm_allowed_var.set(True)

    def _drain_leftover_sessions(self) -> None:
        """Close any session left open by a prior crashed run, then reset state.

        Why: a prior kickoff that exits via os._exit, signal, or thread death
        skips the finally block, so cleanup never runs. Without this, the
        leftover session is reported with the new run's metadata and the next
        ensure_session() short-circuits. force=True so halted leftovers still
        push a terminal WorkflowCompleted — Core never saw the halt verdict.
        """
        assert self._governance_client is not None
        for agent in self._governed_agents():
            if agent._session_started:
                client = agent._governance_client or self._governance_client
                try:
                    agent.close_session(
                        client,
                        agent._crew_name or (self.name or "crew"),
                        {
                            "crew_name": agent._crew_name or (self.name or "crew"),
                            "crew_execution_id": agent._crew_execution_id or "",
                        },
                        status="halted" if agent._halted else "completed",
                        force=True,
                    )
                except Exception:
                    logger.exception("Error closing leftover session for agent %r", agent.role)
            agent._reset_run_state()

    async def _adrain_leftover_sessions(self) -> None:
        assert self._governance_client is not None
        for agent in self._governed_agents():
            if agent._session_started:
                client = agent._governance_client or self._governance_client
                try:
                    await agent._aclose_session(
                        client,
                        agent._crew_name or (self.name or "crew"),
                        {
                            "crew_name": agent._crew_name or (self.name or "crew"),
                            "crew_execution_id": agent._crew_execution_id or "",
                        },
                        status="halted" if agent._halted else "completed",
                        force=True,
                    )
                except Exception:
                    logger.exception("Error closing leftover session for agent %r", agent.role)
            agent._reset_run_state()

    async def _acleanup(self, crew_name: str, metadata: dict[str, Any]) -> None:
        if self._governance_client:
            for agent in self._governed_agents():
                if agent._session_started:
                    status = "halted" if agent._halted else "completed"
                    try:
                        await agent._aclose_session(
                            self._governance_client,
                            crew_name,
                            metadata,
                            status=status,
                        )
                    except Exception:
                        logger.exception("Error closing session for agent %r", agent.role)
                agent._reset_run_state()

        if self._llm_hook:
            unregister_before_llm_call_hook(self._llm_hook)
            self._llm_hook = None

    def _cleanup(self, crew_name: str, metadata: dict[str, Any]) -> None:
        if self._governance_client:
            for agent in self._governed_agents():
                if agent._session_started:
                    status = "halted" if agent._halted else "completed"
                    try:
                        agent.close_session(
                            self._governance_client,
                            crew_name,
                            metadata,
                            status=status,
                        )
                    except Exception:
                        logger.exception("Error closing session for agent %r", agent.role)
                agent._reset_run_state()

        if self._llm_hook:
            unregister_before_llm_call_hook(self._llm_hook)
            self._llm_hook = None


def _make_llm_hook() -> Any:
    def llm_hook(context: Any) -> bool:
        if _llm_allowed_var.get():
            return True
        raise_governance_block(_llm_block_info_var.get(), subject="LLM call")
        return False

    return llm_hook
