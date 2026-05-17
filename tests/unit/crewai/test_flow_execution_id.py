"""Tests for flow_execution_id ContextVar wiring in create_openbox_flow."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from openbox import create_openbox_flow
from openbox.core.payloads import WorkflowStartedPayload
from openbox.utils import _flow_execution_id_var, _multi_agent_session_id_var


@dataclass
class _DummyFlow:
    execution_id: str | None = None
    flow_id: str | None = None
    last_seen_flow_id: str | None = None
    last_seen_session_id: str | None = None

    def kickoff(self, **kwargs: Any) -> str:
        self.last_seen_flow_id = _flow_execution_id_var.get()
        self.last_seen_session_id = _multi_agent_session_id_var.get()
        return "done"


def _make_factory(template: _DummyFlow):
    def factory(**_: Any) -> _DummyFlow:
        return template

    return factory


def test_flow_execution_id_var_is_private() -> None:
    import openbox

    assert "_flow_execution_id_var" not in openbox.__all__
    assert "_multi_agent_session_id_var" not in openbox.__all__


def test_kickoff_sets_both_context_vars() -> None:
    flow = create_openbox_flow(_make_factory(_DummyFlow(execution_id="exec-1")))
    flow.kickoff()
    assert flow.last_seen_flow_id == "exec-1"
    assert flow.last_seen_session_id is not None
    uuid.UUID(flow.last_seen_session_id)


def test_kickoff_resets_flow_execution_id_after_run() -> None:
    flow = create_openbox_flow(_make_factory(_DummyFlow(execution_id="exec-2")))
    flow.kickoff()
    assert _flow_execution_id_var.get() is None
    assert _multi_agent_session_id_var.get() is None


@pytest.mark.parametrize(
    "template,inputs,expected_resolver",
    [
        (_DummyFlow(execution_id="e"), None, lambda f: f.last_seen_flow_id == "e"),
        (
            _DummyFlow(execution_id="e", flow_id="f"),
            None,
            lambda f: f.last_seen_flow_id == "e",
        ),
        (_DummyFlow(flow_id="f"), None, lambda f: f.last_seen_flow_id == "f"),
        (
            _DummyFlow(execution_id="e"),
            {"id": "override"},
            lambda f: f.last_seen_flow_id == "override",
        ),
        (
            _DummyFlow(),
            None,
            lambda f: uuid.UUID(f.last_seen_flow_id) is not None,
        ),
    ],
)
def test_flow_execution_id_resolution(
    template: _DummyFlow, inputs: dict | None, expected_resolver: Any
) -> None:
    flow = create_openbox_flow(_make_factory(template))
    if inputs is None:
        flow.kickoff()
    else:
        flow.kickoff(inputs=inputs)
    assert expected_resolver(flow)


def test_payload_includes_flow_execution_id_when_set() -> None:
    token = _flow_execution_id_var.set("flow-xyz")
    try:
        payload = WorkflowStartedPayload(
            workflow_id="w",
            run_id="r",
            workflow_type="t",
            task_queue="q",
            agent_role="a",
            metadata={},
        )
        d = payload.to_dict()
        assert d["flow_execution_id"] == "flow-xyz"
    finally:
        _flow_execution_id_var.reset(token)


def test_payload_omits_flow_execution_id_when_unset() -> None:
    payload = WorkflowStartedPayload(
        workflow_id="w",
        run_id="r",
        workflow_type="t",
        task_queue="q",
        agent_role="a",
        metadata={},
    )
    d = payload.to_dict()
    assert "flow_execution_id" not in d
