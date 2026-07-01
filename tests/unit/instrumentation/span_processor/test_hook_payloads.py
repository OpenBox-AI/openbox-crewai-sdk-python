"""Verify metadata in Layer 3b (started-stage hook) payloads."""

from __future__ import annotations

from openbox.core.spans import HttpSpanData, Stage
from openbox.instrumentation.interceptors import evaluate_started
from openbox.instrumentation.interceptors._runtime import configure, reset
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import set_current_execution_frame

from ...conftest import (
    CREW_EXEC_ID,
    CREW_NAME,
    MULTI_AGENT_SESSION_ID,
    make_agent_context,
    make_client_mock,
    make_config,
)


class TestHookPayloads:
    def test_hook_started_has_all_metadata(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)

        trace_id = 12345
        ctx = make_agent_context(multi_agent_session_id=MULTI_AGENT_SESSION_ID)
        set_current_execution_frame(ctx, {"activity_id": "act-001", "activity_type": "task"})

        configure(
            span_processor=sp,
            governance_client=client,
            config=config,
            ignored_url_prefixes=set(),
        )

        span_data = HttpSpanData(
            stage=Stage.STARTED,
            span_id="abc123",
            trace_id="def456",
            name="HTTP GET",
        )

        try:
            evaluate_started(trace_id, span_data)
        finally:
            reset()

        payload = client.evaluate.call_args[0][0]
        assert payload["hook_trigger"] is True
        assert payload["metadata"]["crew_name"] == CREW_NAME
        assert payload["metadata"]["crew_execution_id"] == CREW_EXEC_ID
        assert payload["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID
        assert payload["task_queue"] == CREW_NAME

    def test_hook_started_without_flow_id_omits_it(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)

        trace_id = 99999
        ctx = make_agent_context(multi_agent_session_id=None)
        set_current_execution_frame(ctx, {"activity_id": "act-002", "activity_type": "task"})

        configure(
            span_processor=sp,
            governance_client=client,
            config=config,
            ignored_url_prefixes=set(),
        )

        span_data = HttpSpanData(
            stage=Stage.STARTED,
            span_id="abc124",
            trace_id="def457",
            name="HTTP POST",
        )

        try:
            evaluate_started(trace_id, span_data)
        finally:
            reset()

        payload = client.evaluate.call_args[0][0]
        assert payload["metadata"]["crew_name"] == CREW_NAME
        assert payload["metadata"]["crew_execution_id"] == CREW_EXEC_ID
        assert payload.get("multi_agent_session_id") is None
