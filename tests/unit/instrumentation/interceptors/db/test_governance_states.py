"""Tests for governance disabled / fail policies across DB hooks."""

from __future__ import annotations

from unittest.mock import MagicMock

from openbox.instrumentation.interceptors import _runtime
from openbox.instrumentation.interceptors import db as db_gov

from .conftest import active_span


def _make_redis_instance():
    instance = MagicMock()
    instance.connection_pool.connection_kwargs = {"host": "h", "port": 6379, "db": 0}
    return instance


class TestGovernanceDisabled:
    def test_no_governance_when_not_configured(self):
        _runtime._runtime = None
        req_hook, resp_hook = db_gov.setup_redis_hooks()
        instance = _make_redis_instance()
        span = MagicMock()

        with active_span():
            req_hook(span, instance, ("GET", "k"), {})
            resp_hook(span, instance, "OK")

    def test_no_governance_outside_activity(self, governance):
        sp, gc = governance
        sp.get_agent_context.return_value = None
        req_hook, _ = db_gov.setup_redis_hooks()
        instance = _make_redis_instance()
        span = MagicMock()

        with active_span():
            req_hook(span, instance, ("GET", "k"), {})

        assert gc.evaluate.call_count == 0


class TestGovernanceApiErrors:
    # Fixture mocks gc.evaluate directly, bypassing GovernanceClient._handle_api_error.
    # These tests pin the hook-layer swallow behaviour, not on_api_error policy
    # (covered in tests/unit/test_governance_client.py).

    def test_completed_stage_api_error_is_swallowed(self, governance):
        sp, gc = governance
        req_hook, resp_hook = db_gov.setup_redis_hooks()
        instance = _make_redis_instance()
        span = MagicMock()

        gc.evaluate.side_effect = [
            gc.evaluate.return_value,
            ConnectionError("API unavailable"),
        ]

        with active_span():
            req_hook(span, instance, ("GET", "key"), {})
            resp_hook(span, instance, "v")

    def test_started_stage_api_error_is_swallowed(self, governance):
        sp, gc = governance
        gc.evaluate.side_effect = ConnectionError("API unavailable")
        req_hook, _ = db_gov.setup_redis_hooks()
        instance = _make_redis_instance()
        span = MagicMock()

        with active_span():
            req_hook(span, instance, ("GET", "key"), {})
