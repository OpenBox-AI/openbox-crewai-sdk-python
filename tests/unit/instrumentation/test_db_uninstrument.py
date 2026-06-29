"""Wrapt hooks installed by DB interceptors must be torn down on uninstrument."""

from __future__ import annotations

import sys
import types

import wrapt


def _make_fake_module(name: str) -> tuple[types.ModuleType, type]:
    class _Klass:
        def m(self) -> str:
            return "original"

    mod = types.ModuleType(name)
    mod.Klass = _Klass  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return mod, _Klass


def test_uninstrument_all_unwraps_installed_patches() -> None:
    from openbox.instrumentation.interceptors import db

    mod_name = "_fake_db_for_uninstrument_test"
    mod, Klass = _make_fake_module(mod_name)

    def _wrapper(wrapped, instance, args, kwargs):  # type: ignore[no-untyped-def]
        return "wrapped"

    wrapt.wrap_function_wrapper(mod_name, "Klass.m", _wrapper)
    db._installed_patches.append((mod_name, "Klass.m"))
    assert Klass().m() == "wrapped"

    try:
        db.uninstrument_all()
        assert Klass().m() == "original"
        assert (mod_name, "Klass.m") not in db._installed_patches
    finally:
        sys.modules.pop(mod_name, None)


def test_uninstrument_all_resets_per_driver_flags() -> None:
    from openbox.instrumentation.interceptors import db

    db._asyncpg_patched = True
    db._psycopg2_patched = True
    db._aiopg_patched = True
    db._psycopg3_async_patched = True

    db.uninstrument_all()

    assert db._asyncpg_patched is False
    assert db._psycopg2_patched is False
    assert db._aiopg_patched is False
    assert db._psycopg3_async_patched is False


def test_teardown_otel_governance_calls_db_uninstrument_all() -> None:
    from unittest.mock import patch

    from openbox.instrumentation import otel_setup

    otel_setup._instrumented_libraries.add("db_aiopg")
    with patch(
        "openbox.instrumentation.interceptors.db.uninstrument_all"
    ) as uninstrument_all:
        otel_setup.teardown_otel_governance()
    assert uninstrument_all.called
