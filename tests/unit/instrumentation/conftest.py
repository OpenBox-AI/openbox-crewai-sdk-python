from __future__ import annotations

from contextlib import contextmanager
from typing import Generator
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from openbox.core.aip_signing import AgentIdentity
from openbox.core.types import AgentContext, GovernanceResponse, Verdict
from openbox.instrumentation.interceptors import _runtime
from openbox.utils import _current_execution_frame, set_current_execution_frame


@pytest.fixture(autouse=True)
def _tracer_provider():
    if isinstance(otel_trace.get_tracer_provider(), otel_trace.ProxyTracerProvider):
        otel_trace.set_tracer_provider(TracerProvider())


@pytest.fixture(autouse=True)
def _reset_runtime():
    yield
    _runtime._runtime = None
    _current_execution_frame.set(None)


def install_runtime(
    *,
    response: GovernanceResponse | None = None,
    is_aborted: bool = False,
    hitl_enabled: bool = False,
    excluded_crews: set[str] | None = None,
    crew_name: str = "crew-1",
    wait_response: GovernanceResponse | None = None,
    identity: AgentIdentity | None = None,
    activity_type: str = "db_query",
    set_frame: bool = True,
) -> tuple[MagicMock, MagicMock]:
    response = response or GovernanceResponse(verdict=Verdict.ALLOW)
    if set_frame:
        set_current_execution_frame(
            AgentContext(
                role="Tester",
                session_id="sess-1",
                run_id="run-1",
                api_key="obx_test_k",
                crew_name=crew_name,
                crew_execution_id="crew-exec-1",
                identity=identity,
            ),
            {"activity_id": "act-1", "activity_type": activity_type},
        )
    sp = MagicMock()
    sp.is_aborted.return_value = is_aborted
    gc = MagicMock()
    gc.evaluate.return_value = response
    gc.wait_for_approval.return_value = wait_response or response
    config = MagicMock(
        hitl_enabled=hitl_enabled,
        exclude_crews_hitl=excluded_crews or set(),
    )
    _runtime._runtime = _runtime.HookRuntime(
        span_processor=sp,
        governance_client=gc,
        config=config,
        ignored_url_prefixes=set(),
    )
    return sp, gc


@contextmanager
def active_span(name: str = "test.span") -> Generator[otel_trace.Span, None, None]:
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span(name) as span:
        yield span
