from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

import pytest


@pytest.fixture(autouse=True, scope="session")
def _configure_crewai_storage(tmp_path_factory: pytest.TempPathFactory) -> None:
    home_dir = tmp_path_factory.mktemp("crewai-home")
    data_home = home_dir / ".local" / "share"
    data_home.mkdir(parents=True, exist_ok=True)

    old_home = os.environ.get("HOME")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    old_storage = os.environ.get("CREWAI_STORAGE_DIR")

    os.environ["HOME"] = str(home_dir)
    os.environ["XDG_DATA_HOME"] = str(data_home)
    os.environ["CREWAI_STORAGE_DIR"] = "openbox-crewai-sdk-tests"
    try:
        yield
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home

        if old_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_xdg

        if old_storage is None:
            os.environ.pop("CREWAI_STORAGE_DIR", None)
        else:
            os.environ["CREWAI_STORAGE_DIR"] = old_storage


@pytest.fixture
def capture_engine_evaluations(monkeypatch: pytest.MonkeyPatch):
    def _install(engine: Any) -> list[dict[str, Any]]:
        captured: list[dict[str, Any]] = []

        client = engine.client
        original_evaluate = client.evaluate
        original_aevaluate = client.aevaluate

        def wrapped_evaluate(payload, api_key, identity=None):  # type: ignore[no-untyped-def]
            captured.append(
                {
                    "mode": "sync",
                    "payload": deepcopy(payload),
                    "api_key": api_key,
                    "identity": identity,
                }
            )
            return original_evaluate(payload, api_key, identity)

        async def wrapped_aevaluate(payload, api_key, identity=None):  # type: ignore[no-untyped-def]
            captured.append(
                {
                    "mode": "async",
                    "payload": deepcopy(payload),
                    "api_key": api_key,
                    "identity": identity,
                }
            )
            return await original_aevaluate(payload, api_key, identity)

        monkeypatch.setattr(client, "evaluate", wrapped_evaluate)
        monkeypatch.setattr(client, "aevaluate", wrapped_aevaluate)
        return captured

    return _install
