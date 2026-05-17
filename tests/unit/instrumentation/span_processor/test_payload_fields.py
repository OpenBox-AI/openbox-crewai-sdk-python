"""Verify all payload types include required top-level and metadata fields."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from openbox.instrumentation.span_processor import GovernanceSpanProcessor

from ...conftest import (
    API_KEY,
    CREW_EXEC_ID,
    CREW_NAME,
    MULTI_AGENT_SESSION_ID,
    make_agent,
    make_client_mock,
    make_config,
)


class TestPayloadFieldConsistency:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.client = make_client_mock()
        self.config = make_config()
        self.sp = GovernanceSpanProcessor(self.client, self.config)
        self.agent = make_agent()
        with patch.dict(os.environ, {"AGENT_API_KEY": API_KEY}):
            self.agent.configure_governance(
                self.client, self.sp, self.config, CREW_NAME, CREW_EXEC_ID
            )
        self.metadata = {
            "crew_name": CREW_NAME,
            "crew_execution_id": CREW_EXEC_ID,
            "multi_agent_session_id": MULTI_AGENT_SESSION_ID,
        }

    def _assert_common_fields(self, payload: dict[str, Any]) -> None:
        assert payload["source"] == "crewai-telemetry"
        assert payload["task_queue"] == CREW_NAME
        assert payload["agent_role"] == "researcher"
        assert "timestamp" in payload
        assert "metadata" in payload
        meta = payload["metadata"]
        assert meta["crew_name"] == CREW_NAME
        assert meta["crew_execution_id"] == CREW_EXEC_ID
        assert meta["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID

    def test_workflow_started_fields(self) -> None:
        self.agent.ensure_session(self.client, CREW_NAME, self.metadata)
        payload = self.client.evaluate.call_args[0][0]
        self._assert_common_fields(payload)
        assert payload["event_type"] == "WorkflowStarted"
        assert payload["hook_trigger"] is False

    def test_workflow_completed_fields(self) -> None:
        self.agent.ensure_session(self.client, CREW_NAME, self.metadata)
        self.agent.close_session(self.client, CREW_NAME, self.metadata)
        payload = self.client.evaluate.call_args[0][0]
        self._assert_common_fields(payload)
        assert payload["event_type"] == "WorkflowCompleted"
        assert payload["hook_trigger"] is False

    def test_activity_started_fields(self) -> None:
        self.agent._evaluate_task_started(
            self.client, self.config, "task", "policy_test", "act-1", [], self.metadata
        )
        payload = self.client.evaluate.call_args[0][0]
        self._assert_common_fields(payload)
        assert payload["event_type"] == "ActivityStarted"
        assert payload["hook_trigger"] is False

    def test_activity_completed_fields(self) -> None:
        self.agent._evaluate_task_completed(
            self.client, self.config, "task", "policy_test", "act-1", "result", self.metadata
        )
        payload = next(
            call.args[0]
            for call in self.client.evaluate.call_args_list
            if call.args and call.args[0].get("event_type") == "ActivityCompleted"
        )
        self._assert_common_fields(payload)
        assert payload["hook_trigger"] is False
