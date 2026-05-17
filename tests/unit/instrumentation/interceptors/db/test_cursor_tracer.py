"""Tests for CursorTracer monkey-patch governance hooks (DBAPI sync path)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openbox.core.errors import GovernanceBlockedError
from openbox.instrumentation.interceptors import db as db_gov

from .conftest import _make_verdict_response, active_span, first_span_payload


def _make_cursor_tracer(
    db_system: str = "postgresql",
    database: str = "testdb",
    host: str = "pg-host",
    port: int = 5432,
):
    from opentelemetry import trace
    from opentelemetry.instrumentation.dbapi import CursorTracer, DatabaseApiIntegration

    integration = MagicMock(spec=DatabaseApiIntegration)
    integration.database_system = db_system
    integration.database = database
    integration.connection_props = {"host": host, "port": port}
    integration.name = f"{db_system}.{database}"
    integration.capture_parameters = False
    integration.enable_commenter = False
    integration.commenter_options = {}
    integration.enable_attribute_commenter = False
    integration.connect_module = MagicMock()
    integration.span_attributes = {}
    integration._tracer = trace.get_tracer("test-tracer")

    return CursorTracer(integration)


def _make_cursor():
    cursor = MagicMock()
    cursor.execute = MagicMock(return_value=None)
    return cursor


class TestCursorTracerPatch:
    def test_install_patches_cursor_tracer(self):
        from opentelemetry.instrumentation.dbapi import CursorTracer

        original = CursorTracer.traced_execution
        try:
            assert db_gov.install_cursor_tracer_hooks() is True
            assert CursorTracer.traced_execution is not original
        finally:
            db_gov._uninstall_cursor_tracer_hooks()

    def test_double_install_is_safe(self):
        try:
            db_gov.install_cursor_tracer_hooks()
            db_gov.install_cursor_tracer_hooks()
            assert db_gov._orig_traced_execution is not None
        finally:
            db_gov._uninstall_cursor_tracer_hooks()

    def test_uninstall_restores_original(self):
        from opentelemetry.instrumentation.dbapi import CursorTracer

        original = CursorTracer.traced_execution
        db_gov.install_cursor_tracer_hooks()
        db_gov._uninstall_cursor_tracer_hooks()
        assert CursorTracer.traced_execution is original

    def test_traced_execution_sends_started_and_completed(self, governance):
        sp, gc = governance
        db_gov.install_cursor_tracer_hooks()
        tracer = _make_cursor_tracer()
        cursor = _make_cursor()

        with active_span():
            tracer.traced_execution(cursor, cursor.execute, "SELECT * FROM users", None)

        assert gc.evaluate.call_count == 2
        assert gc.evaluate.call_args_list[0].args[0]["hook_trigger"] is True
        assert gc.evaluate.call_args_list[1].args[0]["hook_trigger"] is True
        started = first_span_payload(gc, 0)
        completed = first_span_payload(gc, 1)
        assert started["stage"] == "started"
        assert started["db_system"] == "postgresql"
        assert started["db_operation"] == "SELECT"
        assert started["db_name"] == "testdb"
        assert started["server_address"] == "pg-host"
        assert started["attributes"]["db.system"] == "postgresql"
        assert started["attributes"]["db.operation"] == "SELECT"
        assert started["attributes"]["db.statement"] == "SELECT * FROM users"
        assert started["attributes"]["db.name"] == "testdb"
        assert completed["stage"] == "completed"
        assert completed["duration_ns"] >= 0
        assert completed["attributes"]["db.operation"] == "SELECT"

    def test_traced_execution_blocks_on_halt(self, governance):
        sp, gc = governance
        gc.evaluate.return_value = _make_verdict_response("halt", reason="Blocked")
        db_gov.install_cursor_tracer_hooks()
        tracer = _make_cursor_tracer()
        cursor = _make_cursor()

        with active_span():
            with pytest.raises(GovernanceBlockedError):
                tracer.traced_execution(cursor, cursor.execute, "DROP TABLE users", None)
            cursor.execute.assert_not_called()

    @pytest.mark.parametrize(
        "query,expected_op",
        [
            ("SELECT 1", "SELECT"),
            ("INSERT INTO t VALUES (1)", "INSERT"),
            ("UPDATE t SET x=1", "UPDATE"),
            ("DELETE FROM t", "DELETE"),
            ("CREATE TABLE t (id INT)", "CREATE"),
        ],
    )
    def test_traced_execution_classifies_operations(self, governance, query, expected_op):
        sp, gc = governance
        db_gov.install_cursor_tracer_hooks()
        tracer = _make_cursor_tracer()
        cursor = _make_cursor()

        with active_span():
            tracer.traced_execution(cursor, cursor.execute, query, None)

        assert first_span_payload(gc, 0)["db_operation"] == expected_op

    def test_traced_execution_mysql_system(self, governance):
        sp, gc = governance
        db_gov.install_cursor_tracer_hooks()
        tracer = _make_cursor_tracer(
            db_system="mysql", database="mydb", host="mysql-host", port=3306
        )
        cursor = _make_cursor()

        with active_span():
            tracer.traced_execution(cursor, cursor.execute, "SELECT 1", None)

        started = first_span_payload(gc, 0)
        assert started["db_system"] == "mysql"
        assert started["server_address"] == "mysql-host"
        assert started["server_port"] == 3306

    def test_span_data_has_stage_at_root(self, governance):
        sp, gc = governance
        db_gov.install_cursor_tracer_hooks()
        tracer = _make_cursor_tracer()
        cursor = _make_cursor()

        with active_span():
            tracer.traced_execution(cursor, cursor.execute, "SELECT * FROM users", None)

        started = first_span_payload(gc, 0)
        completed = first_span_payload(gc, 1)

        assert started["stage"] == "started"
        assert completed["stage"] == "completed"
        assert "stage" not in started.get("attributes", {})
        assert "stage" not in completed.get("attributes", {})
        assert "span_id" in started
        assert "trace_id" in started
        assert started["db_system"] == "postgresql"
        assert started["db_operation"] == "SELECT"

    def test_traced_execution_captures_query_error(self, governance):
        sp, gc = governance
        db_gov.install_cursor_tracer_hooks()
        tracer = _make_cursor_tracer()
        cursor = _make_cursor()

        def failing_execute(*args, **kwargs):
            raise RuntimeError("connection lost")

        with active_span():
            with pytest.raises(RuntimeError, match="connection lost"):
                tracer.traced_execution(cursor, failing_execute, "SELECT 1", None)

        assert gc.evaluate.call_count == 2
        completed = first_span_payload(gc, 1)
        assert completed["stage"] == "completed"
        assert completed["error"] == "connection lost"
