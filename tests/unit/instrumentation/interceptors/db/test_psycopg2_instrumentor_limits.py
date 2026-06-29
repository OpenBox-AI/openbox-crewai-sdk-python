"""Pins the OTel assumption behind the CursorTracer patching strategy.

opentelemetry-instrumentation-psycopg2 accepts ``request_hook`` and
``response_hook`` kwargs on ``.instrument(...)`` but silently discards them
on the pinned version. That's why this SDK patches CursorTracer directly
instead of passing hooks to the instrumentor. If this test starts failing,
the instrumentor now honours the hooks and the CursorTracer patch can be
replaced with a plain ``instrument(request_hook=..., response_hook=...)``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor


def test_psycopg2_request_hook_is_silently_discarded():
    request_hook = MagicMock()
    response_hook = MagicMock()

    Psycopg2Instrumentor().instrument(
        request_hook=request_hook,
        response_hook=response_hook,
    )
    try:
        import psycopg2

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        with patch.object(psycopg2, "connect", return_value=mock_conn):
            conn = psycopg2.connect(dbname="test")
            cur = conn.cursor()
            cur.execute("SELECT 1")

        assert not request_hook.called, (
            "Psycopg2Instrumentor now honours request_hook — "
            "replace the CursorTracer patch with instrument(request_hook=...)"
        )
        assert not response_hook.called
    finally:
        Psycopg2Instrumentor().uninstrument()
