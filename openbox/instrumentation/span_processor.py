"""OTel SpanProcessor for governance state and completed-stage evaluation."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.payloads import HookPayload
from openbox.core.spans import SpanData
from openbox.utils import (
    _llm_allowed_var,
    _llm_block_info_var,
    build_metadata,
    get_current_execution_frame,
    truncate_body,
)

logger = logging.getLogger("openbox")


def _get_http_url(attrs: dict[str, Any]) -> str | None:
    return attrs.get("http.url") or attrs.get("url.full") or attrs.get("http.target")


@dataclass(slots=True)
class _DefaultBinding:
    governance_client: GovernanceClient
    config: GovernanceConfig
    ignored_url_prefixes: set[str]


class GovernanceSpanProcessor(SpanProcessor):
    def __init__(
        self,
        governance_client: GovernanceClient | None = None,
        config: GovernanceConfig | None = None,
        ignored_url_prefixes: set[str] | None = None,
        engine: Any | None = None,
    ) -> None:
        self._engine = engine
        self._default_binding = (
            _DefaultBinding(
                governance_client=governance_client,
                config=config,
                ignored_url_prefixes=ignored_url_prefixes or set(),
            )
            if governance_client is not None and config is not None
            else None
        )
        self._lock = threading.Lock()
        self._abort_flags: set[tuple[int, str]] = set()
        self._block_info_by_activity: dict[str, dict[str, Any]] = {}
        self._block_info_by_agent: dict[tuple[int, str], dict[str, Any]] = {}
        self._body_data: dict[int, dict[str, Any]] = {}

    def set_abort(self, trace_id: int, agent_role: str) -> None:
        with self._lock:
            self._abort_flags.add((trace_id, agent_role))

    def is_aborted(self, trace_id: int, agent_role: str) -> bool:
        with self._lock:
            return (trace_id, agent_role) in self._abort_flags

    def set_block_info(
        self,
        *,
        activity_id: str,
        trace_id: int,
        agent_role: str,
        info: dict[str, Any],
    ) -> None:
        with self._lock:
            self._block_info_by_activity[activity_id] = info
            self._block_info_by_agent[(trace_id, agent_role)] = info

    def get_block_info_for_activity(self, activity_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._block_info_by_activity.get(activity_id)

    def get_block_info_for_agent(
        self, trace_id: int, agent_role: str
    ) -> dict[str, Any] | None:
        with self._lock:
            return self._block_info_by_agent.get((trace_id, agent_role))

    def clear_block_info_for_agent(self, trace_id: int, agent_role: str) -> None:
        with self._lock:
            self._block_info_by_agent.pop((trace_id, agent_role), None)

    def store_body(self, span_id: int, key: str, value: str | dict[str, Any]) -> None:
        if isinstance(value, str) and self._default_binding is not None:
            value = truncate_body(value, self._default_binding.config.max_body_size) or ""
        with self._lock:
            if span_id not in self._body_data:
                self._body_data[span_id] = {}
            self._body_data[span_id][key] = value

    def get_body(self, span_id: int) -> dict[str, Any] | None:
        with self._lock:
            return self._body_data.pop(span_id, None)

    def add_ignored_prefix(self, prefix: str) -> None:
        if self._default_binding is None:
            return
        with self._lock:
            self._default_binding.ignored_url_prefixes.add(prefix)

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        """Evaluate completed operation spans against Core."""
        ctx = span.get_span_context()
        if not ctx or not ctx.trace_id:
            return

        attrs = dict(span.attributes) if span.attributes else {}

        span_cls = SpanData.classify(attrs)
        if span_cls is None:
            return

        trace_id = ctx.trace_id

        binding = self._binding_for_trace(trace_id)
        if binding is None:
            return

        # The acting agent comes from the execution frame, which nests and
        # restores through delegation. No frame means no governed agent is
        # executing, so there is nothing to attribute.
        frame = get_current_execution_frame()
        if frame is None:
            return
        agent_ctx = frame.agent_context

        http_url = _get_http_url(attrs)
        if http_url and self._is_ignored_url(http_url, binding["ignored_url_prefixes"]):
            return

        if self.is_aborted(trace_id, agent_ctx.role):
            logger.debug(
                "Skipping completed evaluation — agent %r aborted in trace %s",
                agent_ctx.role,
                trace_id,
            )
            return

        act_ctx = frame.activity_context
        if act_ctx is None:
            return

        body = self.get_body(ctx.span_id)
        span_data = span_cls.from_otel_span(span, attrs, body)

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
            response = binding["governance_client"].evaluate(
                payload.to_dict(), agent_ctx.api_key, agent_ctx.identity
            )
        except Exception:
            logger.exception("Error evaluating completed span %s", span.name)
            return

        # At completed stage, REQUIRE_APPROVAL also aborts — can't poll after execution
        if response.verdict.should_stop() or response.verdict.requires_approval():
            logger.warning(
                "Completed-stage %s verdict for %s: %s",
                response.verdict.value,
                span.name,
                response.reason,
            )
            self.set_abort(trace_id, agent_ctx.role)
            _llm_allowed_var.set(False)
            info = {
                "verdict": response.verdict.value,
                "reason": response.reason,
                "policy_id": response.policy_id,
                "span_name": span.name,
            }
            _llm_block_info_var.set(info)
            self.set_block_info(
                activity_id=task_activity_id,
                trace_id=trace_id,
                agent_role=agent_ctx.role,
                info=info,
            )

    def shutdown(self) -> None:
        if self._default_binding is not None:
            self._default_binding.governance_client.close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def _binding_for_trace(self, trace_id: int) -> dict[str, Any] | None:
        if self._engine is not None and hasattr(self._engine, "binding_for_trace"):
            binding = self._engine.binding_for_trace(trace_id)
            if binding is not None:
                return binding
        if self._default_binding is None:
            return None
        return {
            "governance_client": self._default_binding.governance_client,
            "config": self._default_binding.config,
            "ignored_url_prefixes": self._default_binding.ignored_url_prefixes,
        }

    def _is_ignored_url(self, url: str, ignored_url_prefixes: set[str]) -> bool:
        for prefix in ignored_url_prefixes:
            if url.startswith(prefix):
                return True
        return False
