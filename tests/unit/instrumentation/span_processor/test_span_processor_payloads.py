"""Verify metadata in Layer 3c (completed-stage) payloads via on_end()."""

from __future__ import annotations

from unittest.mock import MagicMock

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


def _make_mock_span(trace_id: int, span_id: int) -> MagicMock:
    mock_span = MagicMock()
    mock_span_ctx = MagicMock()
    mock_span_ctx.trace_id = trace_id
    mock_span_ctx.span_id = span_id
    mock_span.get_span_context.return_value = mock_span_ctx
    mock_span.name = "HTTP GET"
    mock_span.kind = MagicMock(name="CLIENT")
    mock_span.start_time = 1000000000
    mock_span.end_time = 2000000000
    mock_span.parent = None
    mock_span.status = MagicMock()
    mock_span.status.status_code = MagicMock(name="UNSET")
    mock_span.status.description = None
    mock_span.events = []
    return mock_span


class TestSpanProcessorPayloads:
    def test_on_end_has_all_metadata(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)

        trace_id = 55555
        span_id = 77777
        ctx = make_agent_context(multi_agent_session_id=MULTI_AGENT_SESSION_ID)
        set_current_execution_frame(ctx, {"activity_id": "act-003", "activity_type": "task"})

        mock_span = _make_mock_span(trace_id, span_id)
        mock_span.attributes = {"http.method": "GET", "http.url": "https://example.com/api"}

        sp.on_end(mock_span)

        payload = client.evaluate.call_args[0][0]
        assert payload["hook_trigger"] is True
        assert payload["metadata"]["crew_name"] == CREW_NAME
        assert payload["metadata"]["crew_execution_id"] == CREW_EXEC_ID
        assert payload["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID
        assert payload["task_queue"] == CREW_NAME

    def test_on_end_without_flow_id_omits_it(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)

        trace_id = 55556
        span_id = 77778
        ctx = make_agent_context(multi_agent_session_id=None)
        set_current_execution_frame(ctx, {"activity_id": "act-004", "activity_type": "task"})

        mock_span = _make_mock_span(trace_id, span_id)
        mock_span.attributes = {"http.method": "POST", "http.url": "https://example.com/api"}
        mock_span.name = "HTTP POST"

        sp.on_end(mock_span)

        payload = client.evaluate.call_args[0][0]
        assert payload["metadata"]["crew_name"] == CREW_NAME
        assert payload["metadata"]["crew_execution_id"] == CREW_EXEC_ID
        assert payload.get("multi_agent_session_id") is None
