"""Hook runtime state: configure/reset lifecycle, URL filtering, OTel helpers, evaluate_started."""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace

from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.errors import GovernanceAPIError, GovernanceBlockedError
from openbox.core.payloads import HookPayload
from openbox.core.spans import SpanData
from openbox.core.types import AgentContext
from openbox.core.verdict_handler import resolve_verdict
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import (
    _llm_allowed_var,
    _llm_block_info_var,
    build_metadata,
)

logger = logging.getLogger("openbox")


@dataclass(slots=True)
class ExecutionFrame:
    agent_context: AgentContext
    activity_context: dict[str, Any]


@dataclass(slots=True)
class RuntimeBinding:
    span_processor: GovernanceSpanProcessor
    governance_client: GovernanceClient
    config: GovernanceConfig
    ignored_url_prefixes: set[str]


class HookRuntime:
    __slots__ = ("span_processor", "governance_client", "config", "ignored_url_prefixes", "engine")

    def __init__(
        self,
        span_processor: GovernanceSpanProcessor | None = None,
        governance_client: GovernanceClient | None = None,
        config: GovernanceConfig | None = None,
        ignored_url_prefixes: set[str] | None = None,
        engine: object | None = None,
    ) -> None:
        self.span_processor = span_processor
        self.governance_client = governance_client
        self.config = config
        self.ignored_url_prefixes = ignored_url_prefixes or set()
        self.engine = engine

    def binding_for_trace(self, trace_id: int) -> RuntimeBinding | None:
        engine_binding_for_trace = getattr(self.engine, "binding_for_trace", None)
        if engine_binding_for_trace is not None:
            binding = engine_binding_for_trace(trace_id)
            if binding is None:
                return None
            return RuntimeBinding(
                span_processor=binding["span_processor"],
                governance_client=binding["governance_client"],
                config=binding["config"],
                ignored_url_prefixes=binding["ignored_url_prefixes"],
            )

        if (
            self.span_processor is None
            or self.governance_client is None
            or self.config is None
        ):
            return None
        return RuntimeBinding(
            span_processor=self.span_processor,
            governance_client=self.governance_client,
            config=self.config,
            ignored_url_prefixes=self.ignored_url_prefixes,
        )


_runtime: HookRuntime | None = None
_current_execution_frame: contextvars.ContextVar[ExecutionFrame | None] = (
    contextvars.ContextVar("openbox_current_execution_frame", default=None)
)


def set_current_execution_frame(
    agent_context: AgentContext,
    activity_context: dict[str, Any],
) -> contextvars.Token[ExecutionFrame | None]:
    return _current_execution_frame.set(
        ExecutionFrame(agent_context=agent_context, activity_context=activity_context)
    )


def reset_current_execution_frame(
    token: contextvars.Token[ExecutionFrame | None],
) -> None:
    _current_execution_frame.reset(token)


def get_current_execution_frame() -> ExecutionFrame | None:
    return _current_execution_frame.get()


def configure(
    span_processor: GovernanceSpanProcessor | None = None,
    governance_client: GovernanceClient | None = None,
    config: GovernanceConfig | None = None,
    ignored_url_prefixes: set[str] | None = None,
    *,
    engine: object | None = None,
) -> None:
    global _runtime
    _runtime = HookRuntime(
        span_processor=span_processor,
        governance_client=governance_client,
        config=config,
        ignored_url_prefixes=ignored_url_prefixes,
        engine=engine,
    )


def reset() -> None:
    global _runtime
    _runtime = None


def get_runtime() -> HookRuntime | None:
    return _runtime


def should_ignore_url(url: str) -> bool:
    rt = _runtime
    if rt is None:
        return False
    trace_id = get_current_trace_id()
    if trace_id is not None:
        binding = rt.binding_for_trace(trace_id)
        if binding is not None:
            for prefix in binding.ignored_url_prefixes:
                if url.startswith(prefix):
                    return True
            return False
    for prefix in rt.ignored_url_prefixes:
        if url.startswith(prefix):
            return True
    return False


def get_current_trace_id() -> int | None:
    span = trace.get_current_span()
    if span is None:
        return None
    ctx = span.get_span_context()
    if ctx is None or not ctx.trace_id:
        return None
    return ctx.trace_id


def get_current_span_id() -> int | None:
    span = trace.get_current_span()
    if span is None:
        return None
    ctx = span.get_span_context()
    if ctx is None or not ctx.span_id:
        return None
    return ctx.span_id


def evaluate_started(trace_id: int, span_data: SpanData) -> None:
    """Evaluate a started-stage span. Raises GovernanceBlockedError on BLOCK/HALT."""
    rt = _runtime
    if rt is None:
        return

    binding = rt.binding_for_trace(trace_id)
    if binding is None:
        return

    sp = binding.span_processor
    client = binding.governance_client
    config = binding.config

    frame = get_current_execution_frame()
    if frame is not None:
        agent_ctx = frame.agent_context
        act_ctx = frame.activity_context
    else:
        agent_ctx = sp.get_agent_context(trace_id)
        if agent_ctx is None:
            return
        act_ctx = sp.get_activity_context(trace_id)
        if act_ctx is None:
            return

    if sp.is_aborted(trace_id, agent_ctx.role):
        raise GovernanceBlockedError(
            "Operation blocked — task already aborted",
            hook_type=span_data.hook_type,
        )

    task_activity_id = act_ctx["activity_id"]
    task_activity_type = act_ctx.get("activity_type")
    if not task_activity_type:
        return

    span_dict = span_data.to_dict()
    span_dict["activity_id"] = task_activity_id
    payload = HookPayload(
        workflow_id=agent_ctx.session_id,
        run_id=agent_ctx.run_id,
        workflow_type=f"{agent_ctx.role} Agent",
        task_queue=agent_ctx.crew_name,
        activity_id=task_activity_id,
        activity_type=task_activity_type,
        spans=[span_dict],
        agent_role=agent_ctx.role,
        metadata=build_metadata(agent_ctx),
        multi_agent_session_id=agent_ctx.multi_agent_session_id,
    )

    try:
        response = client.evaluate(payload.to_dict(), agent_ctx.api_key, agent_ctx.identity)
    except GovernanceBlockedError:
        raise
    except (GovernanceAPIError, Exception):
        logger.debug("Error evaluating started hook for %s", span_data.name, exc_info=True)
        return

    response = resolve_verdict(
        response,
        "hook",
        config=config,
        crew_name=agent_ctx.crew_name,
        wait_for_approval=lambda: client.wait_for_approval(
            agent_ctx.session_id,
            agent_ctx.run_id,
            task_activity_id,
            agent_ctx.api_key,
            agent_ctx.identity,
        ),
    )

    if response.verdict.should_stop():
        sp.set_abort(trace_id, agent_ctx.role)
        _llm_allowed_var.set(False)
        info = {
            "verdict": response.verdict.value,
            "reason": response.reason,
            "policy_id": response.policy_id,
            "span_name": span_data.name,
        }
        _llm_block_info_var.set(info)
        sp.set_block_info(
            activity_id=task_activity_id,
            trace_id=trace_id,
            agent_role=agent_ctx.role,
            info=info,
        )
        action = "halted" if response.verdict.value == "halt" else "blocked"
        raise GovernanceBlockedError(
            f"Operation {action} by governance: {response.reason}",
            verdict=response.verdict.value,
            reason=response.reason,
            policy_id=response.policy_id,
            hook_type=span_data.hook_type,
        )


def evaluate_completed(trace_id: int, span_data: SpanData) -> None:
    """Best-effort completed-stage telemetry. Never raises."""
    rt = _runtime
    if rt is None:
        return

    binding = rt.binding_for_trace(trace_id)
    if binding is None:
        return

    sp = binding.span_processor
    client = binding.governance_client

    agent_ctx = sp.get_agent_context(trace_id)
    if agent_ctx is None:
        return

    act_ctx = sp.get_activity_context(trace_id)
    if act_ctx is None:
        return
    task_activity_id = act_ctx["activity_id"]
    task_activity_type = act_ctx.get("activity_type")
    if not task_activity_type:
        return

    span_dict = span_data.to_dict()
    span_dict["activity_id"] = task_activity_id
    payload = HookPayload(
        workflow_id=agent_ctx.session_id,
        run_id=agent_ctx.run_id,
        workflow_type=f"{agent_ctx.role} Agent",
        task_queue=agent_ctx.crew_name,
        activity_id=task_activity_id,
        activity_type=task_activity_type,
        spans=[span_dict],
        agent_role=agent_ctx.role,
        metadata=build_metadata(agent_ctx),
        multi_agent_session_id=agent_ctx.multi_agent_session_id,
    )

    try:
        client.evaluate(payload.to_dict(), agent_ctx.api_key, agent_ctx.identity)
    except (GovernanceAPIError, Exception):
        logger.debug("Error sending completed hook for %s", span_data.name, exc_info=True)
