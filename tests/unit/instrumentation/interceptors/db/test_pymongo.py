"""Tests for pymongo governance via CommandListener."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openbox.instrumentation.interceptors import db as db_gov

from .conftest import active_span, first_span_payload


class _EventFactory:
    def __init__(self):
        self._next_request_id = 1

    def _next_id(self, request_id):
        if request_id is None:
            request_id = self._next_request_id
            self._next_request_id += 1
        return request_id

    def started(self, cmd_name="find", db_name="testdb", command=None,
                connection_id=None, request_id=None):
        event = MagicMock()
        event.command_name = cmd_name
        event.database_name = db_name
        event.command = command or {cmd_name: "collection"}
        event.connection_id = connection_id or ("mongo-host", 27017)
        event.request_id = self._next_id(request_id)
        return event

    def succeeded(self, cmd_name="find", db_name="testdb", duration_micros=1500,
                  connection_id=None, request_id=None):
        event = MagicMock()
        event.command_name = cmd_name
        event.database_name = db_name
        event.duration_micros = duration_micros
        event.connection_id = connection_id or ("mongo-host", 27017)
        event.request_id = self._next_id(request_id)
        return event

    def failed(self, cmd_name="find", db_name="testdb", duration_micros=500,
               failure="connection reset", connection_id=None, request_id=None):
        event = MagicMock()
        event.command_name = cmd_name
        event.database_name = db_name
        event.duration_micros = duration_micros
        event.failure = failure
        event.connection_id = connection_id or ("mongo-host", 27017)
        event.request_id = self._next_id(request_id)
        return event


class TestPymongoCommandListener:
    def test_started_sends_governance(self, governance):
        sp, gc = governance
        with patch("pymongo.monitoring.register"):
            db_gov.setup_pymongo_hooks()
        listener = db_gov._pymongo_listener
        events = _EventFactory()

        with active_span():
            listener.started(events.started(cmd_name="insert", db_name="mydb"))

        assert gc.evaluate.call_count == 1
        data = first_span_payload(gc, 0)
        assert data["stage"] == "started"
        assert data["db_system"] == "mongodb"
        assert data["db_name"] == "mydb"
        assert data["db_operation"] == "insert"
        assert data["server_address"] == "mongo-host"
        assert data["server_port"] == 27017

    def test_succeeded_sends_completed_with_consistent_statement(self, governance):
        sp, gc = governance
        with patch("pymongo.monitoring.register"):
            db_gov.setup_pymongo_hooks()
        listener = db_gov._pymongo_listener
        events = _EventFactory()
        req_id = 42

        with active_span():
            listener.started(events.started(
                cmd_name="find", command={"find": "users", "filter": {"age": 30}},
                request_id=req_id,
            ))
            listener.succeeded(events.succeeded(
                cmd_name="find", duration_micros=3000, request_id=req_id,
            ))

        assert gc.evaluate.call_count == 2
        started = first_span_payload(gc, 0)
        completed = first_span_payload(gc, 1)
        assert started["db_statement"] == completed["db_statement"]
        assert completed["stage"] == "completed"
        assert completed["db_system"] == "mongodb"
        assert completed["duration_ns"] == 3_000_000  # 3ms in ns
        assert completed["error"] is None

    def test_failed_sends_completed_with_error(self, governance):
        sp, gc = governance
        with patch("pymongo.monitoring.register"):
            db_gov.setup_pymongo_hooks()
        listener = db_gov._pymongo_listener
        events = _EventFactory()
        req_id = 99

        with active_span():
            listener.started(events.started(
                cmd_name="update", command={"update": "users"}, request_id=req_id,
            ))
            listener.failed(events.failed(
                cmd_name="update", failure="connection timeout", request_id=req_id,
            ))

        assert gc.evaluate.call_count == 2
        started = first_span_payload(gc, 0)
        completed = first_span_payload(gc, 1)
        assert started["db_statement"] == completed["db_statement"]
        assert completed["stage"] == "completed"
        assert completed["error"] == "connection timeout"

    def test_listener_skips_when_wrapt_active(self, governance):
        sp, gc = governance
        with patch("pymongo.monitoring.register"):
            db_gov.setup_pymongo_hooks()
        listener = db_gov._pymongo_listener
        events = _EventFactory()

        db_gov._pymongo_wrapt_depth.value = 1
        try:
            with active_span():
                listener.started(events.started(cmd_name="find"))
                listener.succeeded(events.succeeded(cmd_name="find"))
            assert gc.evaluate.call_count == 0
        finally:
            db_gov._pymongo_wrapt_depth.value = 0

    def test_listener_fires_when_wrapt_inactive(self, governance):
        sp, gc = governance
        with patch("pymongo.monitoring.register"):
            db_gov.setup_pymongo_hooks()
        listener = db_gov._pymongo_listener
        events = _EventFactory()
        req_id = 200

        with active_span():
            listener.started(events.started(cmd_name="endSessions", request_id=req_id))
            listener.succeeded(events.succeeded(cmd_name="endSessions", request_id=req_id))

        assert gc.evaluate.call_count == 2

    def test_address_extraction_fallback(self, governance):
        sp, gc = governance
        with patch("pymongo.monitoring.register"):
            db_gov.setup_pymongo_hooks()
        listener = db_gov._pymongo_listener
        events = _EventFactory()
        event = events.started()
        event.connection_id = None

        with active_span():
            listener.started(event)

        data = first_span_payload(gc, 0)
        assert data["server_address"] == "unknown"
        assert data["server_port"] == 27017
