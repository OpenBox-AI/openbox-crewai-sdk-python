"""Shared fixtures for metadata correlation tests."""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.types import AgentContext, GovernanceResponse, Verdict
from openbox.crewai.agent import OpenBoxAgent

CREW_NAME = "test_crew"
CREW_EXEC_ID = "crew-exec-001"
MULTI_AGENT_SESSION_ID = "multi-agent-session-001"
API_KEY = "obx_test_abc123"

ALLOW_RESPONSE = GovernanceResponse(verdict=Verdict.ALLOW)


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


def make_agent_context(
    *,
    multi_agent_session_id: str | None = None,
) -> AgentContext:
    return AgentContext(
        role="researcher",
        session_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        api_key=API_KEY,
        crew_name=CREW_NAME,
        crew_execution_id=CREW_EXEC_ID,
        multi_agent_session_id=multi_agent_session_id,
    )


def make_config(**overrides: Any) -> GovernanceConfig:
    return GovernanceConfig(**overrides)


def make_client_mock() -> MagicMock:
    mock = MagicMock(spec=GovernanceClient)
    mock.evaluate.return_value = ALLOW_RESPONSE
    mock.aevaluate = AsyncMock(return_value=ALLOW_RESPONSE)
    mock.await_for_approval = AsyncMock(return_value=GovernanceResponse(verdict=Verdict.ALLOW))
    return mock


def make_agent(env_prefix: str = "AGENT") -> OpenBoxAgent:
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fake-key-for-tests"}):
        return OpenBoxAgent(
            role="researcher",
            goal="Research things",
            backstory="Expert researcher",
            env_prefix=env_prefix,
        )
