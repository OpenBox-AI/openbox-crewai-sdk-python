"""Pydantic models for governance payloads sent to Core."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from openbox.utils import (
    SOURCE,
    _flow_execution_id_var,
    _multi_agent_session_id_var,
    rfc3339_now,
)


class _PayloadBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str = SOURCE
    event_type: str
    workflow_id: str
    run_id: str
    workflow_type: str
    task_queue: str
    hook_trigger: bool = False
    timestamp: str = ""
    agent_role: str
    metadata: dict[str, Any]
    multi_agent_session_id: str | None = None
    flow_execution_id: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.timestamp:
            self.timestamp = rfc3339_now()
        if self.multi_agent_session_id is None:
            self.multi_agent_session_id = _multi_agent_session_id_var.get()
        if self.flow_execution_id is None:
            self.flow_execution_id = _flow_execution_id_var.get()

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class WorkflowStartedPayload(_PayloadBase):
    event_type: str = "WorkflowStarted"


class WorkflowCompletedPayload(_PayloadBase):
    event_type: str = "WorkflowCompleted"
    status: str = "completed"


class ActivityStartedPayload(_PayloadBase):
    event_type: str = "ActivityStarted"
    activity_id: str
    activity_type: str
    activity_input: list[dict[str, Any]] | None = None
    attempt: int = 1


class ActivityCompletedPayload(_PayloadBase):
    event_type: str = "ActivityCompleted"
    activity_id: str
    activity_type: str
    activity_output: dict[str, Any] | None = None
    attempt: int = 1


class HookPayload(_PayloadBase):
    event_type: str = "ActivityStarted"
    hook_trigger: bool = True
    activity_id: str
    activity_type: str
    spans: list[dict[str, Any]]
    span_count: int = 1
    attempt: int = 1


class HandoffPayload(_PayloadBase):
    """Emitted when one agent delegates to another within a multi-agent run.

    Carries only the FROM agent's DiD; the TO agent is the authenticated
    emitter resolved server-side from the signed AIP headers. Each emit
    produces one row in core's session_handoffs.
    """

    event_type: str = "Handoff"
    workflow_type: str = "Handoff"
    from_agent_did: str


class SignalReceivedPayload(_PayloadBase):
    event_type: str = "SignalReceived"
    signal_name: str
    signal_args: list[Any]
