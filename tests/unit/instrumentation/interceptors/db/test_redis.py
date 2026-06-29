"""Tests for redis governance hooks (request_hook / response_hook)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openbox.core.errors import GovernanceBlockedError
from openbox.instrumentation.interceptors import db as db_gov

from .conftest import _make_verdict_response, active_span, first_span_payload


def _make_redis_instance():
    instance = MagicMock()
    instance.connection_pool.connection_kwargs = {
        "host": "redis-host",
        "port": 6379,
        "db": 2,
    }
    return instance


class TestRedisHooks:
    def test_request_hook_sends_started(self, governance):
        sp, gc = governance
        req_hook, _ = db_gov.setup_redis_hooks()
        instance = _make_redis_instance()
        span = MagicMock()

        with active_span():
            req_hook(span, instance, ("GET", "mykey"), {})

        assert gc.evaluate.call_count == 1
        data = first_span_payload(gc, 0)
        assert data["hook_type"] == "db_query"
        assert data["stage"] == "started"
        assert data["db_system"] == "redis"
        assert data["db_name"] == "2"
        assert data["db_operation"] == "GET"
        assert data["db_statement"] == "GET mykey"
        assert data["server_address"] == "redis-host"
        assert data["server_port"] == 6379

    def test_request_hook_blocks_on_halt(self, governance):
        sp, gc = governance
        gc.evaluate.return_value = _make_verdict_response("halt", reason="Blocked by policy")
        req_hook, _ = db_gov.setup_redis_hooks()
        instance = _make_redis_instance()
        span = MagicMock()

        with active_span():
            with pytest.raises(GovernanceBlockedError):
                req_hook(span, instance, ("DEL", "sensitive_key"), {})

    def test_response_hook_sends_completed(self, governance):
        sp, gc = governance
        req_hook, resp_hook = db_gov.setup_redis_hooks()
        instance = _make_redis_instance()
        span = MagicMock()

        with active_span():
            req_hook(span, instance, ("SET", "key", "val"), {})
            resp_hook(span, instance, "OK")

        assert gc.evaluate.call_count == 2
        completed = first_span_payload(gc, 1)
        assert completed["stage"] == "completed"
        assert completed["db_system"] == "redis"
        assert completed["duration_ns"] >= 0
