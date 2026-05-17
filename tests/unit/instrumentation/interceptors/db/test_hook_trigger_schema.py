"""Verify hook_trigger payloads contain all required fields."""

from __future__ import annotations

from unittest.mock import MagicMock

from openbox.instrumentation.interceptors import db as db_gov

from .conftest import active_span, first_span_payload


def _make_redis_instance():
    instance = MagicMock()
    instance.connection_pool.connection_kwargs = {"host": "h", "port": 6379, "db": 0}
    return instance


class TestHookTriggerSchema:
    def test_started_trigger_has_required_fields(self, governance):
        sp, gc = governance
        req_hook, _ = db_gov.setup_redis_hooks()
        instance = _make_redis_instance()
        span = MagicMock()

        with active_span():
            req_hook(span, instance, ("HSET", "myhash", "field", "value"), {})

        payload = gc.evaluate.call_args_list[0].args[0]
        assert payload["hook_trigger"] is True
        data = payload["spans"][0]
        required = {
            "hook_type", "stage", "db_system", "db_name",
            "db_operation", "db_statement", "server_address", "server_port",
        }
        assert required.issubset(data.keys())
        assert data["hook_type"] == "db_query"
        assert data["stage"] == "started"

    def test_completed_trigger_has_duration_and_error(self, governance):
        sp, gc = governance
        req_hook, resp_hook = db_gov.setup_redis_hooks()
        instance = _make_redis_instance()
        span = MagicMock()

        with active_span():
            req_hook(span, instance, ("GET", "k"), {})
            resp_hook(span, instance, "v")

        data = first_span_payload(gc, 1)
        assert "duration_ns" in data
        assert "error" in data
        assert data["duration_ns"] >= 0
        assert data["error"] is None
