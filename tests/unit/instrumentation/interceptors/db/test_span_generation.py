"""Regression tests for outer-agent-span leak into DB payload span.name."""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from openbox.instrumentation.interceptors import db as db_gov


class TestBuildDbSpanData:
    def test_uses_current_otel_span_name_when_available(self):
        fake_span = MagicMock()
        fake_span.name = "SELECT"
        fake_span.get_span_context.return_value = MagicMock(
            span_id=0x1234, trace_id=0xABCD, is_valid=True
        )
        fake_span.parent = None
        fake_span.attributes = {}

        data = db_gov._build_db_span_data(
            fake_span, "postgresql", "mydb", "SELECT",
            "SELECT 1", "pg-host", 5432, "started",
        )
        assert data["name"] == "SELECT"

    def test_falls_back_to_constructed_name_when_span_name_missing(self):
        fake_span = MagicMock()
        fake_span.name = None
        fake_span.get_span_context.return_value = MagicMock(
            span_id=0x1234, trace_id=0xABCD, is_valid=True
        )
        fake_span.parent = None
        fake_span.attributes = {}

        data = db_gov._build_db_span_data(
            fake_span, "postgresql", "mydb", "INSERT",
            "INSERT INTO t VALUES (1)", "pg-host", 5432, "started",
        )
        assert data["name"] == "INSERT postgresql"

    def test_mapping_proxy_attributes_preserved_in_payload(self):
        if isinstance(otel_trace.get_tracer_provider(), otel_trace.ProxyTracerProvider):
            otel_trace.set_tracer_provider(TracerProvider())
        tracer = otel_trace.get_tracer("test.attrs")
        with tracer.start_as_current_span("real.span") as real_span:
            real_span.set_attribute("db.user", "alice")
            real_span.set_attribute("custom.tag", "x")

            assert isinstance(real_span.attributes, types.MappingProxyType)

            data = db_gov._build_db_span_data(
                real_span, "postgresql", "mydb", "SELECT",
                "SELECT 1", "pg-host", 5432, "started",
            )

        assert data["attributes"]["db.user"] == "alice"
        assert data["attributes"]["custom.tag"] == "x"


class TestGovernedQueryAsyncSpanName:
    @pytest.mark.asyncio
    async def test_outer_non_db_span_does_not_leak_into_payload(self, governance):
        sp, gc = governance

        tracer = otel_trace.get_tracer("probe")
        with tracer.start_as_current_span("agent.Tester"):
            async def _fake_query(sql):
                return None

            await db_gov._run_governed_query_async(
                _fake_query, ("CREATE TABLE x (id int)",), {},
                db_system="postgresql", db_name="mydb", operation="CREATE",
                stmt="CREATE TABLE x (id int)", host="pg-host", port=5432,
            )

        names = [c.args[0]["spans"][0]["name"] for c in gc.evaluate.call_args_list]
        assert names
        for n in names:
            assert n == "CREATE postgresql", (
                f"expected DB-shaped span name, got {n!r} — outer agent span leaked"
            )

    @pytest.mark.asyncio
    async def test_no_outer_span_still_produces_db_name(self, governance):
        sp, gc = governance

        async def _fake_query(sql):
            return None

        await db_gov._run_governed_query_async(
            _fake_query, ("SELECT 1",), {},
            db_system="postgresql", db_name="mydb", operation="SELECT",
            stmt="SELECT 1", host="pg-host", port=5432,
        )

        assert gc.evaluate.call_count >= 1
        assert gc.evaluate.call_args_list[0].args[0]["spans"][0]["name"] == "SELECT postgresql"
