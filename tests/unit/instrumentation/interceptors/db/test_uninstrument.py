"""Tests for uninstrument_all cleanup."""

from __future__ import annotations

from openbox.instrumentation.interceptors import db as db_gov


class TestUninstrument:
    def test_uninstrument_clears_sqlalchemy_listeners(self, governance):
        from sqlalchemy import create_engine

        engine = create_engine("sqlite:///:memory:")
        db_gov.setup_sqlalchemy_hooks(engine)
        assert len(db_gov._sqlalchemy_listeners) == 3  # before, after, handle_error

        db_gov.uninstrument_all()
        assert len(db_gov._sqlalchemy_listeners) == 0

    def test_uninstrument_clears_patch_list(self):
        db_gov._installed_patches.append(("test.module", "func"))
        db_gov.uninstrument_all()
        assert len(db_gov._installed_patches) == 0

    def test_uninstrument_restores_cursor_tracer(self):
        from opentelemetry.instrumentation.dbapi import CursorTracer

        original = CursorTracer.traced_execution
        db_gov.install_cursor_tracer_hooks()
        assert CursorTracer.traced_execution is not original

        db_gov.uninstrument_all()
        assert CursorTracer.traced_execution is original
