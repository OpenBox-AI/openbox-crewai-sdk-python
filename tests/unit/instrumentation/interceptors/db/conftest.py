"""Shared fixtures for DB governance hook unit tests."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from openbox.instrumentation.interceptors import _runtime
from openbox.instrumentation.interceptors import db as db_gov


@pytest.fixture(autouse=True)
def _tracer_provider():
    # OTel ProxyTracerProvider can't open recording spans; install a real one.
    current = otel_trace.get_tracer_provider()
    if isinstance(current, otel_trace.ProxyTracerProvider):
        otel_trace.set_tracer_provider(TracerProvider())


@pytest.fixture(autouse=True)
def _cleanup_hooks():
    yield
    db_gov.uninstrument_all()
    _runtime._runtime = None


def _make_verdict_response(action: str = "allow", reason: str | None = None) -> MagicMock:
    verdict = MagicMock()
    verdict.value = action
    verdict.should_stop = lambda: action in ("halt", "block", "stop")
    verdict.requires_approval = lambda: action == "require_approval"

    response = MagicMock()
    response.verdict = verdict
    response.reason = reason
    response.policy_id = None
    return response


def _setup_governance(on_api_error: str = "fail_open") -> tuple[MagicMock, MagicMock]:
    sp = MagicMock()
    sp.get_agent_context.return_value = MagicMock(
        session_id="sess-1",
        run_id="run-1",
        role="Tester",
        crew_name="crew-1",
        api_key="k",
        identity=None,
        multi_agent_session_id=None,
    )
    sp.is_aborted.return_value = False
    sp.get_activity_context.return_value = {
        "activity_id": "act-1",
        "activity_type": "db_query",
    }

    gc = MagicMock()
    gc.evaluate.return_value = _make_verdict_response("allow")

    config = MagicMock(
        hitl_enabled=False, exclude_crews_hitl=set(), on_api_error=on_api_error
    )

    _runtime._runtime = _runtime.HookRuntime(
        span_processor=sp,
        governance_client=gc,
        config=config,
        ignored_url_prefixes=set(),
    )
    db_gov.configure(sp)
    return sp, gc


@pytest.fixture
def governance() -> tuple[MagicMock, MagicMock]:
    return _setup_governance()


@contextmanager
def active_span(name: str = "test.span") -> Generator[otel_trace.Span, None, None]:
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span(name) as span:
        yield span


def first_span_payload(gc: MagicMock, call_index: int = 0) -> dict:
    call = gc.evaluate.call_args_list[call_index]
    payload = call.args[0] if call.args else call.kwargs.get("payload")
    return payload["spans"][0]


def payload_at(gc: MagicMock, call_index: int) -> dict:
    call = gc.evaluate.call_args_list[call_index]
    return call.args[0] if call.args else call.kwargs.get("payload")
