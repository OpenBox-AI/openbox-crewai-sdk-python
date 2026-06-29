"""aiopg and psycopg3 are wrapt-only drivers — no OTel instrumentor exists."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch


def test_aiopg_in_db_target_set_no_unknown_warning(caplog) -> None:
    from openbox.instrumentation.otel_setup import _instrument_databases

    with patch(
        "openbox.instrumentation.interceptors.db.install_aiopg_hooks",
        MagicMock(return_value=True),
    ) as install_aiopg, patch(
        "openbox.instrumentation.interceptors.db.install_cursor_tracer_hooks",
        MagicMock(return_value=True),
    ):
        with caplog.at_level(logging.WARNING, logger="openbox"):
            _instrument_databases({"aiopg"})
    assert install_aiopg.called
    assert not any("Unknown DB library: aiopg" in r.message for r in caplog.records)


def test_psycopg3_in_db_target_set_no_unknown_warning(caplog) -> None:
    from openbox.instrumentation.otel_setup import _instrument_databases

    with patch(
        "openbox.instrumentation.interceptors.db.install_psycopg3_async_hooks",
        MagicMock(return_value=True),
    ) as install_psycopg3, patch(
        "openbox.instrumentation.interceptors.db.install_cursor_tracer_hooks",
        MagicMock(return_value=True),
    ):
        with caplog.at_level(logging.WARNING, logger="openbox"):
            _instrument_databases({"psycopg3"})
    assert install_psycopg3.called
    assert not any("Unknown DB library: psycopg3" in r.message for r in caplog.records)
