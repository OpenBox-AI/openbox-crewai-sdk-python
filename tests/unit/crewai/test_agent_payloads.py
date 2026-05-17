"""Verify metadata in Layer 1 (pre/post task) and session payloads."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from openbox.instrumentation.span_processor import GovernanceSpanProcessor

from ..conftest import (
    API_KEY,
    CREW_EXEC_ID,
    CREW_NAME,
    MULTI_AGENT_SESSION_ID,
    make_agent,
    make_client_mock,
    make_config,
)


class TestAgentPayloads:
    @pytest.fixture(autouse=True)
    def setup_agent(self) -> None:
        self.client = make_client_mock()
        self.config = make_config()
        self.sp = GovernanceSpanProcessor(self.client, self.config)
        self.agent = make_agent()
        with patch.dict(os.environ, {"AGENT_API_KEY": API_KEY}):
            self.agent.configure_governance(
                self.client, self.sp, self.config, CREW_NAME, CREW_EXEC_ID
            )

    def _get_last_payload(self) -> dict[str, Any]:
        return self.client.evaluate.call_args[0][0]

    # ── ensure_session (WorkflowStarted) ─────────────────────────────────

    def test_session_start_has_crew_name(self) -> None:
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        self.agent.ensure_session(self.client, CREW_NAME, metadata)
        payload = self._get_last_payload()
        assert payload["metadata"]["crew_name"] == CREW_NAME
        assert payload["task_queue"] == CREW_NAME

    def test_session_start_has_crew_execution_id(self) -> None:
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        self.agent.ensure_session(self.client, CREW_NAME, metadata)
        payload = self._get_last_payload()
        assert payload["metadata"]["crew_execution_id"] == CREW_EXEC_ID

    def test_session_start_has_multi_agent_session_id(self) -> None:
        metadata = {
            "crew_name": CREW_NAME,
            "crew_execution_id": CREW_EXEC_ID,
            "multi_agent_session_id": MULTI_AGENT_SESSION_ID,
        }
        self.agent.ensure_session(self.client, CREW_NAME, metadata)
        payload = self._get_last_payload()
        assert payload["metadata"]["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID

    # ── close_session (WorkflowCompleted) ────────────────────────────────

    def test_session_close_has_all_metadata(self) -> None:
        metadata = {
            "crew_name": CREW_NAME,
            "crew_execution_id": CREW_EXEC_ID,
            "multi_agent_session_id": MULTI_AGENT_SESSION_ID,
        }
        self.agent.ensure_session(self.client, CREW_NAME, metadata)
        self.agent.close_session(self.client, CREW_NAME, metadata)
        payload = self._get_last_payload()
        assert payload["event_type"] == "WorkflowCompleted"
        assert payload["metadata"]["crew_name"] == CREW_NAME
        assert payload["metadata"]["crew_execution_id"] == CREW_EXEC_ID
        assert payload["metadata"]["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID
        assert payload["task_queue"] == CREW_NAME

    # ── _evaluate_task_started (ActivityStarted) ─────────────────────────

    def test_task_started_has_all_metadata(self) -> None:
        metadata = {
            "crew_name": CREW_NAME,
            "crew_execution_id": CREW_EXEC_ID,
            "multi_agent_session_id": MULTI_AGENT_SESSION_ID,
        }
        self.agent._evaluate_task_started(
            self.client, self.config, "research_task", "policy_test", "act-001", [], metadata
        )
        payload = self._get_last_payload()
        assert payload["event_type"] == "ActivityStarted"
        assert payload["metadata"]["crew_name"] == CREW_NAME
        assert payload["metadata"]["crew_execution_id"] == CREW_EXEC_ID
        assert payload["metadata"]["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID
        assert payload["task_queue"] == CREW_NAME

    # ── _evaluate_task_completed (ActivityCompleted) ─────────────────────

    def test_task_completed_has_all_metadata(self) -> None:
        metadata = {
            "crew_name": CREW_NAME,
            "crew_execution_id": CREW_EXEC_ID,
            "multi_agent_session_id": MULTI_AGENT_SESSION_ID,
        }
        self.agent._evaluate_task_completed(
            self.client, self.config, "research_task", "policy_test", "act-001", "result", metadata
        )
        activity_completed = next(
            call.args[0]
            for call in self.client.evaluate.call_args_list
            if call.args and call.args[0].get("event_type") == "ActivityCompleted"
        )
        assert activity_completed["metadata"]["crew_name"] == CREW_NAME
        assert activity_completed["metadata"]["crew_execution_id"] == CREW_EXEC_ID
        assert activity_completed["metadata"]["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID
        assert activity_completed["task_queue"] == CREW_NAME

    # ── SignalReceived (user_input / agent_output, per-task) ─────────────

    def _payloads_of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [
            call.args[0]
            for call in self.client.evaluate.call_args_list
            if call.args and call.args[0].get("event_type") == event_type
        ]

    def test_user_input_signal_emitted_per_task_with_description(self) -> None:
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        activity_input = [
            {"description": "Research AI safety"},
            {"expected_output": "A summary"},
        ]
        self.agent._evaluate_task_started(
            self.client, self.config, "t", "policy_test", "a", activity_input, metadata
        )
        signals = self._payloads_of_type("SignalReceived")
        assert len(signals) == 1
        assert signals[0]["signal_name"] == "user_input"
        assert signals[0]["signal_args"] == ["Research AI safety"]
        assert signals[0]["task_queue"] == CREW_NAME

    def test_user_input_signal_skipped_when_no_description(self) -> None:
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        self.agent._evaluate_task_started(
            self.client, self.config, "t", "policy_test", "a", [], metadata
        )
        assert self._payloads_of_type("SignalReceived") == []

    def test_agent_output_signal_emitted_per_task_completed(self) -> None:
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        self.agent._evaluate_task_completed(
            self.client, self.config, "t", "policy_test", "a", "the answer", metadata
        )
        signals = self._payloads_of_type("SignalReceived")
        agent_outputs = [s for s in signals if s["signal_name"] == "agent_output"]
        assert len(agent_outputs) == 1
        assert agent_outputs[0]["signal_args"] == ["the answer"]

    def test_session_lifecycle_emits_no_signals(self) -> None:
        metadata = {"crew_name": CREW_NAME, "crew_execution_id": CREW_EXEC_ID}
        self.agent.ensure_session(self.client, CREW_NAME, metadata)
        self.agent.close_session(self.client, CREW_NAME, metadata)
        assert self._payloads_of_type("SignalReceived") == []
