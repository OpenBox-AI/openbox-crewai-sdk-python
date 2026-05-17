from __future__ import annotations

import pytest

from openbox.core.types import AgentContext
from openbox.core.aip_signing import AgentIdentity
from openbox.core.errors import GovernanceBlockedError
from openbox.core.spans.base import Stage
from openbox.core.spans.db import DbSpanData
from openbox.core.types import GovernanceResponse, Verdict
from openbox.instrumentation.interceptors import _runtime

from .conftest import install_runtime as _install_runtime


def _make_span() -> DbSpanData:
    return DbSpanData(
        stage=Stage.STARTED,
        span_id="span-1",
        trace_id="trace-1",
        name="SELECT users",
        db_system="postgresql",
        db_operation="SELECT",
        db_statement="SELECT 1",
    )


def test_already_aborted_short_circuits() -> None:
    sp, gc = _install_runtime(
        response=GovernanceResponse(verdict=Verdict.ALLOW),
        is_aborted=True,
    )

    with pytest.raises(GovernanceBlockedError, match="already aborted"):
        _runtime.evaluate_started(trace_id=123, span_data=_make_span())

    assert gc.evaluate.call_count == 0
    assert gc.wait_for_approval.call_count == 0


def test_not_aborted_proceeds_to_evaluate() -> None:
    sp, gc = _install_runtime(
        response=GovernanceResponse(verdict=Verdict.ALLOW),
        is_aborted=False,
    )

    _runtime.evaluate_started(trace_id=123, span_data=_make_span())

    assert gc.evaluate.call_count == 1


def test_require_approval_triggers_wait_when_hitl_enabled() -> None:
    sp, gc = _install_runtime(
        response=GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL),
        hitl_enabled=True,
        wait_response=GovernanceResponse(verdict=Verdict.ALLOW, reason="approved"),
    )

    _runtime.evaluate_started(trace_id=123, span_data=_make_span())

    assert gc.wait_for_approval.call_count == 1


def test_require_approval_skips_wait_when_hitl_disabled() -> None:
    sp, gc = _install_runtime(
        response=GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL),
        hitl_enabled=False,
    )

    _runtime.evaluate_started(trace_id=123, span_data=_make_span())

    assert gc.wait_for_approval.call_count == 0


def test_require_approval_skips_wait_when_crew_excluded() -> None:
    sp, gc = _install_runtime(
        response=GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL),
        hitl_enabled=True,
        excluded_crews={"crew-1"},
        crew_name="crew-1",
    )

    _runtime.evaluate_started(trace_id=123, span_data=_make_span())

    assert gc.wait_for_approval.call_count == 0


def test_wait_for_approval_then_block_raises() -> None:
    sp, gc = _install_runtime(
        response=GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL),
        hitl_enabled=True,
        wait_response=GovernanceResponse(
            verdict=Verdict.BLOCK,
            reason="denied by reviewer",
            policy_id="pol-1",
        ),
    )

    with pytest.raises(GovernanceBlockedError, match="denied by reviewer"):
        _runtime.evaluate_started(trace_id=123, span_data=_make_span())

    assert gc.wait_for_approval.call_count == 1
    sp.set_abort.assert_called_once_with(123, "Tester")


def test_constrain_logs_reason_and_proceeds(caplog) -> None:
    import logging

    sp, gc = _install_runtime(
        response=GovernanceResponse(
            verdict=Verdict.CONSTRAIN,
            reason="read-only mode",
            policy_id="pol-7",
        ),
    )

    with caplog.at_level(logging.INFO, logger="openbox"):
        _runtime.evaluate_started(trace_id=123, span_data=_make_span())

    assert gc.evaluate.call_count == 1
    sp.set_abort.assert_not_called()
    assert any(
        "Governance constraint applied [hook]: read-only mode (policy: pol-7)" in r.message
        for r in caplog.records
    )


def test_wait_for_approval_forwards_agent_identity() -> None:
    identity = AgentIdentity(
        did="did:aip:11111111-1111-1111-1111-111111111111",
        private_key="A" * 44,
    )
    sp, gc = _install_runtime(
        response=GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL),
        hitl_enabled=True,
        wait_response=GovernanceResponse(verdict=Verdict.ALLOW),
        identity=identity,
    )

    _runtime.evaluate_started(trace_id=123, span_data=_make_span())

    args, kwargs = gc.wait_for_approval.call_args
    forwarded = kwargs.get("identity") if "identity" in kwargs else args[-1]
    assert forwarded is identity


def test_current_execution_frame_overrides_trace_level_context() -> None:
    sp, gc = _install_runtime(
        response=GovernanceResponse(verdict=Verdict.ALLOW),
    )
    sp.get_agent_context.return_value = AgentContext(
        role="Worker",
        session_id="sess-worker",
        run_id="run-worker",
        api_key="obx_test_worker",
        crew_name="crew-worker",
        crew_execution_id="crew-exec-worker",
    )
    sp.get_activity_context.return_value = {
        "activity_id": "act-worker",
        "activity_type": "worker_query",
    }

    manager_ctx = AgentContext(
        role="Manager",
        session_id="sess-manager",
        run_id="run-manager",
        api_key="obx_test_manager",
        crew_name="crew-manager",
        crew_execution_id="crew-exec-manager",
    )
    token = _runtime.set_current_execution_frame(
        manager_ctx,
        {
            "activity_id": "act-manager",
            "activity_type": "manager_query",
        },
    )
    try:
        _runtime.evaluate_started(trace_id=123, span_data=_make_span())
    finally:
        _runtime.reset_current_execution_frame(token)

    payload = gc.evaluate.call_args[0][0]
    assert payload["workflow_id"] == "sess-manager"
    assert payload["run_id"] == "run-manager"
    assert payload["agent_role"] == "Manager"
    assert payload["activity_id"] == "act-manager"
    assert payload["activity_type"] == "manager_query"
    assert payload["task_queue"] == "crew-manager"


def test_nested_execution_frame_reset_restores_outer_context() -> None:
    _, gc = _install_runtime(
        response=GovernanceResponse(verdict=Verdict.ALLOW),
    )

    manager_ctx = AgentContext(
        role="Manager",
        session_id="sess-manager",
        run_id="run-manager",
        api_key="obx_test_manager",
        crew_name="crew-manager",
        crew_execution_id="crew-exec-manager",
    )
    worker_ctx = AgentContext(
        role="Worker",
        session_id="sess-worker",
        run_id="run-worker",
        api_key="obx_test_worker",
        crew_name="crew-worker",
        crew_execution_id="crew-exec-worker",
    )

    outer = _runtime.set_current_execution_frame(
        manager_ctx,
        {
            "activity_id": "act-manager",
            "activity_type": "manager_query",
        },
    )
    try:
        inner = _runtime.set_current_execution_frame(
            worker_ctx,
            {
                "activity_id": "act-worker",
                "activity_type": "worker_query",
            },
        )
        try:
            _runtime.evaluate_started(trace_id=123, span_data=_make_span())
        finally:
            _runtime.reset_current_execution_frame(inner)

        _runtime.evaluate_started(trace_id=123, span_data=_make_span())
    finally:
        _runtime.reset_current_execution_frame(outer)

    first_payload = gc.evaluate.call_args_list[0][0][0]
    second_payload = gc.evaluate.call_args_list[1][0][0]
    assert first_payload["agent_role"] == "Worker"
    assert first_payload["activity_id"] == "act-worker"
    assert second_payload["agent_role"] == "Manager"
    assert second_payload["activity_id"] == "act-manager"
