"""OpenBoxAgent — CrewAI Agent with per-agent governance."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from typing import Any

from crewai import Agent, Task
from opentelemetry import trace
from pydantic import PrivateAttr

from openbox.core.aip_signing import (
    AgentIdentity,
    validate_agent_did,
    validate_ed25519_private_key,
)
from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.errors import (
    GovernanceBlockedError,
    GovernanceHaltError,
    OpenBoxConfigError,
    raise_governance_block,
)
from openbox.core.payloads import (
    ActivityCompletedPayload,
    ActivityStartedPayload,
    HandoffPayload,
    SignalReceivedPayload,
    WorkflowCompletedPayload,
    WorkflowStartedPayload,
)
from openbox.core.types import AgentContext, GovernanceResponse, Verdict
from openbox.core.verdict_handler import resolve_verdict, resolve_verdict_async
from openbox.crewai.task import OpenBoxTask
from openbox.engine import OpenBoxEngine
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import (
    _handoff_origin_did_var,
    _llm_allowed_var,
    _llm_block_info_var,
    _multi_agent_session_id_var,
    reset_current_execution_frame,
    rfc3339_now,
    set_current_execution_frame,
    validate_api_key_format,
)

logger = logging.getLogger("openbox")


def _description_from_activity_input(
    activity_input: list[dict[str, Any]],
) -> str | None:
    for item in activity_input:
        if isinstance(item, dict) and "description" in item:
            return str(item["description"])
    return None


class OpenBoxAgent(Agent):
    env_prefix: str

    _openbox_api_key: str = PrivateAttr(default="")
    _openbox_identity: AgentIdentity | None = PrivateAttr(default=None)
    _session_id: str | None = PrivateAttr(default=None)
    _run_id: str | None = PrivateAttr(default=None)
    _halted: bool = PrivateAttr(default=False)
    _session_started: bool = PrivateAttr(default=False)
    _session_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _asession_lock: asyncio.Lock | None = PrivateAttr(default=None)

    _span_processor: GovernanceSpanProcessor | None = PrivateAttr(default=None)
    _governance_client: GovernanceClient | None = PrivateAttr(default=None)
    _governance_engine: OpenBoxEngine | None = PrivateAttr(default=None)
    _config: GovernanceConfig | None = PrivateAttr(default=None)
    _crew_name: str = PrivateAttr(default="")
    _crew_execution_id: str = PrivateAttr(default="")

    def _env_var(self, suffix: str) -> str:
        return f"{self.env_prefix.rstrip('_')}_{suffix}"

    def get_delegation_tools(self, agents: Any) -> list[Any]:
        """Return delegation tools that propagate GovernanceHaltError.

        Stock crewai DelegateWorkTool / AskQuestionTool swallow exceptions from
        the delegated agent (base_agent_tools.py:130). Override here so a HALT
        raised by a delegated OpenBoxAgent aborts the crew/flow.
        """
        from openbox.crewai.delegation_tools import make_openbox_delegation_tools

        return make_openbox_delegation_tools(agents)

    def _reset_run_state(self) -> None:
        self._session_id = None
        self._run_id = None
        self._session_started = False
        self._halted = False

    def _emit_signal(
        self,
        client: GovernanceClient,
        signal_name: str,
        signal_args: list[Any],
        metadata: dict[str, Any],
    ) -> None:
        payload = SignalReceivedPayload(
            workflow_id=self._session_id or "",
            run_id=self._run_id or "",
            workflow_type=f"{self.role} Agent",
            task_queue=self._crew_name,
            agent_role=self.role,
            metadata=metadata,
            signal_name=signal_name,
            signal_args=signal_args,
        )
        client.evaluate(payload.to_dict(), self._openbox_api_key, self._openbox_identity)

    async def _aemit_signal(
        self,
        client: GovernanceClient,
        signal_name: str,
        signal_args: list[Any],
        metadata: dict[str, Any],
    ) -> None:
        payload = SignalReceivedPayload(
            workflow_id=self._session_id or "",
            run_id=self._run_id or "",
            workflow_type=f"{self.role} Agent",
            task_queue=self._crew_name,
            agent_role=self.role,
            metadata=metadata,
            signal_name=signal_name,
            signal_args=signal_args,
        )
        await client.aevaluate(payload.to_dict(), self._openbox_api_key, self._openbox_identity)

    def _should_emit_handoff(self, handoff_origin_did: str | None) -> bool:
        if not handoff_origin_did:
            return False
        if not self._openbox_identity:
            return False
        # Retries within the same agent re-enter execute_task with the
        # contextvar still set to this agent's own DID — that is not a
        # handoff, skip it. Without this guard core persists phantom
        # session_handoffs rows where from_agent_id == to_agent_id.
        if handoff_origin_did == self._openbox_identity.did:
            return False
        if not _multi_agent_session_id_var.get():
            return False
        return True

    def configure_governance(
        self,
        client: GovernanceClient,
        span_processor: GovernanceSpanProcessor,
        config: GovernanceConfig,
        crew_name: str,
        crew_execution_id: str,
        engine: OpenBoxEngine | None = None,
    ) -> None:
        """Inject governance references and resolve API key.

        API key is resolved here (not at construction) so users can
        construct OpenBoxAgent before env vars are set.
        """
        api_key_env = self._env_var("API_KEY")
        key = os.environ.get(api_key_env)
        if not key:
            raise OpenBoxConfigError(f"Environment variable {api_key_env!r} is not set")
        validate_api_key_format(key, api_key_env)
        self._openbox_api_key = key
        self._openbox_identity = self._resolve_identity()

        self._governance_client = client
        self._governance_engine = engine
        self._span_processor = span_processor
        self._config = config
        self._crew_name = crew_name
        self._crew_execution_id = crew_execution_id

        self._install_llm_pre_check()

    def _install_llm_pre_check(self) -> None:
        if self.llm is None or self._span_processor is None:
            return
        llm: Any = self.llm
        sp = self._span_processor
        role = self.role

        if getattr(llm, "_openbox_pre_check_installed", False):
            return

        original_call = llm.call
        original_acall = getattr(llm, "acall", None)

        def _check() -> None:
            current = trace.get_current_span()
            if current is None:
                return
            ctx = current.get_span_context()
            if ctx is None or not ctx.trace_id:
                return
            info = sp.get_block_info_for_agent(ctx.trace_id, role)
            if info:
                raise_governance_block(info, subject="LLM call")

        def governed_call(*args: Any, **kwargs: Any) -> Any:
            _check()
            return original_call(*args, **kwargs)

        llm.call = governed_call

        if original_acall is not None:
            async def governed_acall(*args: Any, **kwargs: Any) -> Any:
                _check()
                return await original_acall(*args, **kwargs)

            llm.acall = governed_acall

        llm._openbox_pre_check_installed = True

    def _resolve_identity(self) -> AgentIdentity | None:
        did_env = self._env_var("DID")
        pk_env = self._env_var("PRIVATE_KEY")
        did = os.environ.get(did_env)
        pk = os.environ.get(pk_env)
        if not did and not pk:
            return None
        if not did or not pk:
            raise OpenBoxConfigError(
                f"Environment variables {did_env!r} and {pk_env!r} must be set together"
            )
        if not validate_agent_did(did):
            raise OpenBoxConfigError(
                f"Invalid agent DID format in {did_env!r}; expected did:aip:<uuid>"
            )
        if not validate_ed25519_private_key(pk):
            raise OpenBoxConfigError(
                f"Invalid Ed25519 private key in {pk_env!r}; expected base64-encoded 32 bytes"
            )
        return AgentIdentity(did=did, private_key=pk)

    def execute_task(
        self,
        task: Any,
        context: str | None = None,
        tools: Any | None = None,
    ) -> str:
        # CrewAI's delegate_work_to_coworker tool dispatches to workers with
        # a plain crewai.Task; coerce so delegated work stays governed.
        if not isinstance(task, OpenBoxTask) and isinstance(task, Task):
            task = OpenBoxTask(
                description=task.description,
                expected_output=task.expected_output,
                agent=task.agent,
                activity_type="delegation",
            )

        if not isinstance(task, OpenBoxTask):
            return super().execute_task(task, context, tools)

        if self._halted:
            logger.warning(
                "Agent %r refused task — halted by a previous governance verdict in this kickoff",
                self.role,
            )
            raise GovernanceHaltError(
                f"Agent {self.role!r} is halted",
                verdict="halt",
                reason="Agent was halted by a previous governance verdict",
            )

        if self._span_processor is None or self._governance_client is None:
            return super().execute_task(task, context, tools)

        client = self._governance_client
        config = self._config
        assert config is not None

        metadata: dict[str, Any] = {
            "crew_name": self._crew_name,
            "crew_execution_id": self._crew_execution_id,
        }

        handoff_origin_did = _handoff_origin_did_var.get()

        self.ensure_session(client, self._crew_name, metadata)

        if self._should_emit_handoff(handoff_origin_did):
            assert handoff_origin_did is not None
            handoff_payload = HandoffPayload(
                workflow_id=self._session_id or "",
                run_id=self._run_id or "",
                task_queue=self._crew_name,
                agent_role=self.role,
                metadata=metadata,
                from_agent_did=handoff_origin_did,
                timestamp=rfc3339_now(),
            )
            try:
                client.evaluate(
                    handoff_payload.to_dict(),
                    self._openbox_api_key,
                    self._openbox_identity,
                )
            except Exception as exc:  # never block work on telemetry
                logger.warning("Handoff emission failed: %s", exc)

        task_name = task.name or "unnamed_task"
        activity_type = task.activity_type
        activity_id = str(uuid.uuid4())

        activity_input: list[dict[str, Any]] = []
        task_desc = getattr(task, "description", None)
        if task_desc:
            activity_input.append({"description": str(task_desc)})
        task_expected = getattr(task, "expected_output", None)
        if task_expected:
            activity_input.append({"expected_output": str(task_expected)})

        # Layer 1: pre-task
        if config.send_task_start_event:
            layer1_response = self._evaluate_task_started(
                client, config, task_name, activity_type, activity_id, activity_input, metadata
            )

            gr = layer1_response.guardrails_result
            if gr and gr.input_type == "activity_input" and gr.redacted_input is not None:
                redacted = gr.redacted_input
                if isinstance(redacted, list):
                    for item in redacted:
                        if isinstance(item, dict) and "description" in item:
                            task.description = item["description"]
                            logger.info("Task input redacted by governance for %r", task_name)
                            break
                elif isinstance(redacted, dict) and "description" in redacted:
                    task.description = redacted["description"]
                    logger.info("Task input redacted by governance for %r", task_name)

        # Execute within OTel root span
        tracer = trace.get_tracer("openbox")
        with tracer.start_as_current_span(f"agent.{self.role}") as span:
            trace_id = span.get_span_context().trace_id
            agent_ctx = AgentContext(
                role=self.role,
                session_id=self._session_id or "",
                run_id=self._run_id or "",
                api_key=self._openbox_api_key,
                crew_name=self._crew_name,
                crew_execution_id=self._crew_execution_id,
                multi_agent_session_id=_multi_agent_session_id_var.get(),
                identity=self._openbox_identity,
            )
            activity_ctx = {
                "activity_id": activity_id,
                "activity_type": activity_type,
            }
            existing = self._span_processor.get_block_info_for_agent(
                trace_id, self.role
            )
            if existing:
                raise_governance_block(existing, subject="LLM call")

            _llm_allowed_var.set(True)
            _llm_block_info_var.set(None)
            origin_did = (
                self._openbox_identity.did if self._openbox_identity else None
            )
            origin_token = _handoff_origin_did_var.set(origin_did)
            frame_token = set_current_execution_frame(agent_ctx, activity_ctx)
            try:
                result = super().execute_task(task, context, tools)
            except (GovernanceBlockedError, GovernanceHaltError):
                raise
            except Exception:
                info = (
                    self._span_processor.get_block_info_for_activity(activity_id)
                    or self._span_processor.get_block_info_for_agent(
                        trace_id, self.role
                    )
                )
                if info:
                    raise_governance_block(info, subject="LLM call")
                raise
            else:
                info = (
                    self._span_processor.get_block_info_for_activity(activity_id)
                    or self._span_processor.get_block_info_for_agent(
                        trace_id, self.role
                    )
                )
                if info:
                    raise_governance_block(info, subject="LLM call")
            finally:
                reset_current_execution_frame(frame_token)
                _handoff_origin_did_var.reset(origin_token)

        # Layer 2: post-task
        if config.send_task_completed_event:
            result = self._evaluate_task_completed(
                client, config, task_name, activity_type, activity_id, result, metadata
            )

        return result

    def _evaluate_task_started(
        self,
        client: GovernanceClient,
        config: GovernanceConfig,
        task_name: str,
        activity_type: str,
        activity_id: str,
        activity_input: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> GovernanceResponse:
        if config.exclude_crews and self._crew_name in config.exclude_crews:
            return GovernanceResponse(verdict=Verdict.ALLOW)

        description = _description_from_activity_input(activity_input)
        if description:
            self._emit_signal(client, "user_input", [description], metadata)

        payload = ActivityStartedPayload(
            workflow_id=self._session_id or "",
            run_id=self._run_id or "",
            workflow_type=f"{self.role} Agent",
            task_queue=self._crew_name,
            activity_id=activity_id,
            activity_type=activity_type,
            activity_input=activity_input or None,
            agent_role=self.role,
            metadata=metadata,
        )

        response = client.evaluate(payload.to_dict(), self._openbox_api_key, self._openbox_identity)

        response = resolve_verdict(
            response,
            "task_start",
            config=config,
            crew_name=self._crew_name,
            wait_for_approval=lambda: client.wait_for_approval(
                self._session_id or "",
                self._run_id or "",
                activity_id,
                self._openbox_api_key,
                self._openbox_identity,
            ),
        )

        if response.verdict is Verdict.HALT:
            self._halted = True
            _llm_allowed_var.set(False)
            raise GovernanceHaltError(
                f"Task halted: {response.reason}",
                verdict=response.verdict.value,
                reason=response.reason,
                policy_id=response.policy_id,
            )
        if response.verdict is Verdict.BLOCK:
            raise GovernanceBlockedError(
                f"Task blocked: {response.reason}",
                verdict=response.verdict.value,
                reason=response.reason,
                policy_id=response.policy_id,
            )

        return response

    def _evaluate_task_completed(
        self,
        client: GovernanceClient,
        config: GovernanceConfig,
        task_name: str,
        activity_type: str,
        activity_id: str,
        result: str,
        metadata: dict[str, Any],
    ) -> str:
        if config.exclude_crews and self._crew_name in config.exclude_crews:
            return result

        payload = ActivityCompletedPayload(
            workflow_id=self._session_id or "",
            run_id=self._run_id or "",
            workflow_type=f"{self.role} Agent",
            task_queue=self._crew_name,
            activity_id=activity_id,
            activity_type=activity_type,
            activity_output={"result": result},
            agent_role=self.role,
            metadata=metadata,
        )

        response = client.evaluate(payload.to_dict(), self._openbox_api_key, self._openbox_identity)

        response = resolve_verdict(
            response,
            "task_end",
            config=config,
            crew_name=self._crew_name,
            wait_for_approval=lambda: client.wait_for_approval(
                self._session_id or "",
                self._run_id or "",
                activity_id,
                self._openbox_api_key,
                self._openbox_identity,
            ),
        )

        if response.verdict is Verdict.HALT:
            self._halted = True
            _llm_allowed_var.set(False)
            raise GovernanceHaltError(
                f"Task output halted: {response.reason}",
                verdict=response.verdict.value,
                reason=response.reason,
                policy_id=response.policy_id,
            )
        if response.verdict is Verdict.BLOCK:
            raise GovernanceBlockedError(
                f"Task output blocked: {response.reason}",
                verdict=response.verdict.value,
                reason=response.reason,
                policy_id=response.policy_id,
            )

        gr = response.guardrails_result
        if gr and gr.input_type == "activity_output" and gr.redacted_input is not None:
            logger.info("Task output redacted by governance for %r", task_name)
            redacted = gr.redacted_input
            if isinstance(redacted, dict) and "result" in redacted:
                final = str(redacted["result"])
            else:
                final = str(redacted)
        else:
            final = result

        self._emit_signal(client, "agent_output", [final], metadata)
        return final

    async def aexecute_task(
        self,
        task: Any,
        context: str | None = None,
        tools: Any | None = None,
    ) -> str:
        if not isinstance(task, OpenBoxTask) and isinstance(task, Task):
            task = OpenBoxTask(
                description=task.description,
                expected_output=task.expected_output,
                agent=task.agent,
                activity_type="delegation",
            )

        if not isinstance(task, OpenBoxTask):
            return await super().aexecute_task(task, context, tools)

        if self._halted:
            logger.warning(
                "Agent %r refused task — halted by a previous governance verdict in this kickoff",
                self.role,
            )
            raise GovernanceHaltError(
                f"Agent {self.role!r} is halted",
                verdict="halt",
                reason="Agent was halted by a previous governance verdict",
            )

        if self._span_processor is None or self._governance_client is None:
            return await super().aexecute_task(task, context, tools)

        client = self._governance_client
        config = self._config
        assert config is not None

        metadata: dict[str, Any] = {
            "crew_name": self._crew_name,
            "crew_execution_id": self._crew_execution_id,
        }

        handoff_origin_did = _handoff_origin_did_var.get()

        await self._aensure_session(client, self._crew_name, metadata)

        if self._should_emit_handoff(handoff_origin_did):
            assert handoff_origin_did is not None
            handoff_payload = HandoffPayload(
                workflow_id=self._session_id or "",
                run_id=self._run_id or "",
                task_queue=self._crew_name,
                agent_role=self.role,
                metadata=metadata,
                from_agent_did=handoff_origin_did,
                timestamp=rfc3339_now(),
            )
            try:
                await client.aevaluate(
                    handoff_payload.to_dict(),
                    self._openbox_api_key,
                    self._openbox_identity,
                )
            except Exception as exc:
                logger.warning("Handoff emission failed: %s", exc)

        task_name = task.name or "unnamed_task"
        activity_type = task.activity_type
        activity_id = str(uuid.uuid4())

        activity_input: list[dict[str, Any]] = []
        task_desc = getattr(task, "description", None)
        if task_desc:
            activity_input.append({"description": str(task_desc)})
        task_expected = getattr(task, "expected_output", None)
        if task_expected:
            activity_input.append({"expected_output": str(task_expected)})

        # Layer 1: pre-task
        if config.send_task_start_event:
            layer1_response = await self._aevaluate_task_started(
                client, config, task_name, activity_type, activity_id, activity_input, metadata
            )

            gr = layer1_response.guardrails_result
            if gr and gr.input_type == "activity_input" and gr.redacted_input is not None:
                redacted = gr.redacted_input
                if isinstance(redacted, list):
                    for item in redacted:
                        if isinstance(item, dict) and "description" in item:
                            task.description = item["description"]
                            logger.info("Task input redacted by governance for %r", task_name)
                            break
                elif isinstance(redacted, dict) and "description" in redacted:
                    task.description = redacted["description"]
                    logger.info("Task input redacted by governance for %r", task_name)

        # Execute within OTel root span
        tracer = trace.get_tracer("openbox")
        with tracer.start_as_current_span(f"agent.{self.role}") as span:
            trace_id = span.get_span_context().trace_id
            agent_ctx = AgentContext(
                role=self.role,
                session_id=self._session_id or "",
                run_id=self._run_id or "",
                api_key=self._openbox_api_key,
                crew_name=self._crew_name,
                crew_execution_id=self._crew_execution_id,
                multi_agent_session_id=_multi_agent_session_id_var.get(),
                identity=self._openbox_identity,
            )
            activity_ctx = {
                "activity_id": activity_id,
                "activity_type": activity_type,
            }
            existing = self._span_processor.get_block_info_for_agent(
                trace_id, self.role
            )
            if existing:
                raise_governance_block(existing, subject="LLM call")

            _llm_allowed_var.set(True)
            _llm_block_info_var.set(None)
            origin_did = (
                self._openbox_identity.did if self._openbox_identity else None
            )
            origin_token = _handoff_origin_did_var.set(origin_did)
            frame_token = set_current_execution_frame(agent_ctx, activity_ctx)
            try:
                result = await super().aexecute_task(task, context, tools)
            except (GovernanceBlockedError, GovernanceHaltError):
                raise
            except Exception:
                info = (
                    self._span_processor.get_block_info_for_activity(activity_id)
                    or self._span_processor.get_block_info_for_agent(
                        trace_id, self.role
                    )
                )
                if info:
                    raise_governance_block(info, subject="LLM call")
                raise
            else:
                info = (
                    self._span_processor.get_block_info_for_activity(activity_id)
                    or self._span_processor.get_block_info_for_agent(
                        trace_id, self.role
                    )
                )
                if info:
                    raise_governance_block(info, subject="LLM call")
            finally:
                reset_current_execution_frame(frame_token)
                _handoff_origin_did_var.reset(origin_token)

        # Layer 2: post-task
        if config.send_task_completed_event:
            result = await self._aevaluate_task_completed(
                client, config, task_name, activity_type, activity_id, result, metadata
            )

        return result

    async def _aevaluate_task_started(
        self,
        client: GovernanceClient,
        config: GovernanceConfig,
        task_name: str,
        activity_type: str,
        activity_id: str,
        activity_input: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> GovernanceResponse:
        if config.exclude_crews and self._crew_name in config.exclude_crews:
            return GovernanceResponse(verdict=Verdict.ALLOW)

        description = _description_from_activity_input(activity_input)
        if description:
            await self._aemit_signal(client, "user_input", [description], metadata)

        payload = ActivityStartedPayload(
            workflow_id=self._session_id or "",
            run_id=self._run_id or "",
            workflow_type=f"{self.role} Agent",
            task_queue=self._crew_name,
            activity_id=activity_id,
            activity_type=activity_type,
            activity_input=activity_input or None,
            agent_role=self.role,
            metadata=metadata,
        )

        response = await client.aevaluate(
            payload.to_dict(), self._openbox_api_key, self._openbox_identity
        )

        response = await resolve_verdict_async(
            response,
            "task_start",
            config=config,
            crew_name=self._crew_name,
            await_for_approval=lambda: client.await_for_approval(
                self._session_id or "",
                self._run_id or "",
                activity_id,
                self._openbox_api_key,
                self._openbox_identity,
            ),
        )

        if response.verdict is Verdict.HALT:
            self._halted = True
            _llm_allowed_var.set(False)
            raise GovernanceHaltError(
                f"Task halted: {response.reason}",
                verdict=response.verdict.value,
                reason=response.reason,
                policy_id=response.policy_id,
            )
        if response.verdict is Verdict.BLOCK:
            raise GovernanceBlockedError(
                f"Task blocked: {response.reason}",
                verdict=response.verdict.value,
                reason=response.reason,
                policy_id=response.policy_id,
            )

        return response

    async def _aevaluate_task_completed(
        self,
        client: GovernanceClient,
        config: GovernanceConfig,
        task_name: str,
        activity_type: str,
        activity_id: str,
        result: str,
        metadata: dict[str, Any],
    ) -> str:
        if config.exclude_crews and self._crew_name in config.exclude_crews:
            return result

        payload = ActivityCompletedPayload(
            workflow_id=self._session_id or "",
            run_id=self._run_id or "",
            workflow_type=f"{self.role} Agent",
            task_queue=self._crew_name,
            activity_id=activity_id,
            activity_type=activity_type,
            activity_output={"result": result},
            agent_role=self.role,
            metadata=metadata,
        )

        response = await client.aevaluate(
            payload.to_dict(), self._openbox_api_key, self._openbox_identity
        )

        response = await resolve_verdict_async(
            response,
            "task_end",
            config=config,
            crew_name=self._crew_name,
            await_for_approval=lambda: client.await_for_approval(
                self._session_id or "",
                self._run_id or "",
                activity_id,
                self._openbox_api_key,
                self._openbox_identity,
            ),
        )

        if response.verdict is Verdict.HALT:
            self._halted = True
            _llm_allowed_var.set(False)
            raise GovernanceHaltError(
                f"Task output halted: {response.reason}",
                verdict=response.verdict.value,
                reason=response.reason,
                policy_id=response.policy_id,
            )
        if response.verdict is Verdict.BLOCK:
            raise GovernanceBlockedError(
                f"Task output blocked: {response.reason}",
                verdict=response.verdict.value,
                reason=response.reason,
                policy_id=response.policy_id,
            )

        gr = response.guardrails_result
        if gr and gr.input_type == "activity_output" and gr.redacted_input is not None:
            logger.info("Task output redacted by governance for %r", task_name)
            redacted = gr.redacted_input
            if isinstance(redacted, dict) and "result" in redacted:
                final = str(redacted["result"])
            else:
                final = str(redacted)
        else:
            final = result

        await self._aemit_signal(client, "agent_output", [final], metadata)
        return final

    async def _aensure_session(
        self,
        client: GovernanceClient,
        crew_name: str,
        metadata: dict[str, Any],
    ) -> None:
        """Lazily start governance session on first task (async). Coroutine-safe."""
        if self._session_started:
            return
        if self._asession_lock is None:
            self._asession_lock = asyncio.Lock()
        async with self._asession_lock:
            if self._session_started:
                return
            self._session_id = str(uuid.uuid4())
            self._run_id = str(uuid.uuid4())

            payload = WorkflowStartedPayload(
                workflow_id=self._session_id or "",
                run_id=self._run_id or "",
                workflow_type=f"{self.role} Agent",
                task_queue=crew_name,
                agent_role=self.role,
                metadata=metadata,
            )
            await client.aevaluate(payload.to_dict(), self._openbox_api_key, self._openbox_identity)
            self._session_started = True

        logger.info("Session started for agent %r (session=%s)", self.role, self._session_id)

    async def _aclose_session(
        self,
        client: GovernanceClient,
        crew_name: str,
        metadata: dict[str, Any],
        *,
        status: str = "completed",
        force: bool = False,
    ) -> None:
        if not self._session_started:
            return
        if self._halted and not force:
            # Core already terminated the session on the halt verdict during
            # this kickoff. Sending another WorkflowCompleted would conflict.
            # Drain-from-prior-crash callers pass force=True to push a final
            # WorkflowCompleted(status=halted) that Core never received.
            return

        payload = WorkflowCompletedPayload(
            workflow_id=self._session_id or "",
            run_id=self._run_id or "",
            workflow_type=f"{self.role} Agent",
            task_queue=crew_name,
            agent_role=self.role,
            status=status,
            metadata=metadata,
        )
        await client.aevaluate(payload.to_dict(), self._openbox_api_key, self._openbox_identity)
        logger.info(
            "Session closed for agent %r (session=%s, status=%s)",
            self.role,
            self._session_id,
            status,
        )

    def ensure_session(
        self,
        client: GovernanceClient,
        crew_name: str,
        metadata: dict[str, Any],
    ) -> None:
        """Lazily start governance session on first task. Thread-safe."""
        if self._session_started:
            return
        with self._session_lock:
            if self._session_started:
                return
            self._session_id = str(uuid.uuid4())
            self._run_id = str(uuid.uuid4())

            payload = WorkflowStartedPayload(
                workflow_id=self._session_id or "",
                run_id=self._run_id or "",
                workflow_type=f"{self.role} Agent",
                task_queue=crew_name,
                agent_role=self.role,
                metadata=metadata,
            )
            client.evaluate(payload.to_dict(), self._openbox_api_key, self._openbox_identity)
            self._session_started = True

        logger.info("Session started for agent %r (session=%s)", self.role, self._session_id)

    def close_session(
        self,
        client: GovernanceClient,
        crew_name: str,
        metadata: dict[str, Any],
        *,
        status: str = "completed",
        force: bool = False,
    ) -> None:
        if not self._session_started:
            return
        if self._halted and not force:
            # Core already terminated the session on the halt verdict during
            # this kickoff. Sending another WorkflowCompleted would conflict.
            # Drain-from-prior-crash callers pass force=True to push a final
            # WorkflowCompleted(status=halted) that Core never received.
            return

        payload = WorkflowCompletedPayload(
            workflow_id=self._session_id or "",
            run_id=self._run_id or "",
            workflow_type=f"{self.role} Agent",
            task_queue=crew_name,
            agent_role=self.role,
            status=status,
            metadata=metadata,
        )
        client.evaluate(payload.to_dict(), self._openbox_api_key, self._openbox_identity)
        logger.info(
            "Session closed for agent %r (session=%s, status=%s)",
            self.role,
            self._session_id,
            status,
        )
