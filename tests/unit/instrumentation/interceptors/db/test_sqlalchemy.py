"""Tests for SQLAlchemy governance hooks via before/after_cursor_execute events."""

from __future__ import annotations

import pytest

from openbox.core.errors import GovernanceBlockedError
from openbox.instrumentation.interceptors import db as db_gov

from .conftest import _make_verdict_response, active_span, first_span_payload


def _make_engine():
    from sqlalchemy import create_engine

    return create_engine("sqlite:///:memory:")


class TestSQLAlchemyHooks:
    def test_select_sends_started_and_completed(self, governance):
        from sqlalchemy import text

        sp, gc = governance
        engine = _make_engine()
        db_gov.setup_sqlalchemy_hooks(engine)

        with active_span():
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))

        assert gc.evaluate.call_count == 2
        started = first_span_payload(gc, 0)
        completed = first_span_payload(gc, 1)
        assert started["stage"] == "started"
        assert started["db_operation"] == "SELECT"
        assert started["db_system"] == "sqlite"
        assert completed["stage"] == "completed"
        assert completed["duration_ns"] >= 0

    def test_block_prevents_query(self, governance):
        from sqlalchemy import inspect, text

        sp, gc = governance
        gc.evaluate.return_value = _make_verdict_response("block", reason="Query blocked")
        engine = _make_engine()
        db_gov.setup_sqlalchemy_hooks(engine)

        with active_span():
            with pytest.raises(GovernanceBlockedError):
                with engine.connect() as conn:
                    conn.execute(text("CREATE TABLE forbidden (id INT)"))

        assert not inspect(engine).has_table("forbidden"), (
            "blocked CREATE TABLE still landed — started-stage block did not prevent execution"
        )

    def test_insert_sends_correct_operation(self, governance):
        from sqlalchemy import text

        sp, gc = governance
        engine = _make_engine()
        db_gov.setup_sqlalchemy_hooks(engine)

        with active_span():
            with engine.connect() as conn:
                conn.execute(text("CREATE TABLE t (id INT)"))
                conn.execute(text("INSERT INTO t VALUES (1)"))

        insert_calls = [
            c for c in gc.evaluate.call_args_list
            if c.args[0]["spans"][0]["db_operation"] == "INSERT"
        ]
        assert len(insert_calls) >= 1
        assert insert_calls[0].args[0]["spans"][0]["stage"] == "started"
