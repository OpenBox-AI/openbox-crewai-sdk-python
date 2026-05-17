"""Engine-level tests."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from openbox.core.aip_signing import AgentIdentity
from openbox.core.config import GovernanceConfig
from openbox.core.errors import OpenBoxAuthError, OpenBoxConfigError
from openbox.engine import OpenBoxEngine

API_KEY = "obx_test_aaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_KEY = "obx_test_bbbbbbbbbbbbbbbbbbbbbbbb"


@dataclass
class FakeAgent:
    env_prefix: str
    identity: AgentIdentity | None = None

    def _resolve_identity(self) -> AgentIdentity | None:
        return self.identity


def _engine_with_mock_client() -> tuple[OpenBoxEngine, MagicMock]:
    engine = OpenBoxEngine(api_url="http://localhost:8080", config=GovernanceConfig())
    mock_client = MagicMock()
    engine._client = mock_client  # type: ignore[assignment]
    return engine, mock_client


def test_two_agents_sharing_env_prefix_validate_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBOX_RESEARCHER_API_KEY", API_KEY)
    engine, mock_client = _engine_with_mock_client()

    engine.validate_agent_api_keys(
        [FakeAgent(env_prefix="OPENBOX_RESEARCHER"), FakeAgent(env_prefix="OPENBOX_RESEARCHER")]
    )

    assert mock_client.validate_api_key.call_count == 1


def test_two_agents_different_env_same_key_value_both_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENBOX_RESEARCHER_API_KEY", API_KEY)
    monkeypatch.setenv("OPENBOX_WRITER_API_KEY", API_KEY)
    engine, mock_client = _engine_with_mock_client()

    engine.validate_agent_api_keys(
        [FakeAgent(env_prefix="OPENBOX_RESEARCHER"), FakeAgent(env_prefix="OPENBOX_WRITER")]
    )

    assert mock_client.validate_api_key.call_count == 2


def test_each_agent_validates_with_its_own_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBOX_A_API_KEY", API_KEY)
    monkeypatch.setenv("OPENBOX_B_API_KEY", API_KEY)
    engine, mock_client = _engine_with_mock_client()

    id_a = AgentIdentity(
        did="did:aip:ece81073-60e1-5091-b284-cfd247823a77",
        private_key="a" * 44,
    )
    id_b = AgentIdentity(
        did="did:aip:11111111-2222-5333-9444-555555555555",
        private_key="b" * 44,
    )

    engine.validate_agent_api_keys(
        [FakeAgent(env_prefix="OPENBOX_A", identity=id_a), FakeAgent(env_prefix="OPENBOX_B", identity=id_b)]
    )

    identities_passed = [call.args[1] for call in mock_client.validate_api_key.call_args_list]
    assert id_a in identities_passed
    assert id_b in identities_passed


def test_missing_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENBOX_RESEARCHER_API_KEY", raising=False)
    engine, _ = _engine_with_mock_client()

    with pytest.raises(OpenBoxConfigError, match="OPENBOX_RESEARCHER_API_KEY"):
        engine.validate_agent_api_keys([FakeAgent(env_prefix="OPENBOX_RESEARCHER")])


def test_invalid_key_format_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBOX_BAD_API_KEY", "not-a-valid-key")
    engine, _ = _engine_with_mock_client()

    with pytest.raises(OpenBoxAuthError):
        engine.validate_agent_api_keys([FakeAgent(env_prefix="OPENBOX_BAD")])
