"""Tests for GovernanceClient.validate_api_key."""

from __future__ import annotations

import httpx
import pytest
from crewai import Crew

from openbox import create_openbox_engine
from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.constants import AUTH_VALIDATE_PATH
from openbox.core.errors import OpenBoxAuthError, OpenBoxNetworkError
from openbox.crewai.agent import OpenBoxAgent

API_URL = "https://api.openbox.ai"
API_KEY = "obx_test_valid_key"


def _client_with_transport(handler: httpx.MockTransport) -> GovernanceClient:
    client = GovernanceClient(API_URL, GovernanceConfig())
    client._http.close()
    client._http = httpx.Client(transport=handler, timeout=client._config.api_timeout)
    return client


class TestValidateApiKeySync:
    def test_sends_bearer_token_to_auth_endpoint(self) -> None:
        observed: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["authorization"] = request.headers.get("authorization", "")
            observed["url"] = str(request.url)
            observed["method"] = request.method
            return httpx.Response(200, json={"ok": True})

        client = _client_with_transport(httpx.MockTransport(handler))
        client.validate_api_key(API_KEY)
        client.close()

        assert observed["authorization"] == f"Bearer {API_KEY}"
        assert observed["url"] == f"{API_URL}{AUTH_VALIDATE_PATH}"
        assert observed["method"] == "GET"

    @pytest.mark.parametrize("status", [401, 403])
    def test_raises_auth_error_on_401_403(self, status: int) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": "invalid"})

        client = _client_with_transport(httpx.MockTransport(handler))
        with pytest.raises(OpenBoxAuthError):
            client.validate_api_key(API_KEY)
        client.close()

    def test_raises_network_error_on_5xx(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        client = _client_with_transport(httpx.MockTransport(handler))
        with pytest.raises(OpenBoxNetworkError):
            client.validate_api_key(API_KEY)
        client.close()

    def test_raises_network_error_on_connection_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _client_with_transport(httpx.MockTransport(handler))
        with pytest.raises(OpenBoxNetworkError):
            client.validate_api_key(API_KEY)
        client.close()


class TestCreateOpenBoxCrewValidatesApiKey:
    def test_raises_auth_error_at_crew_creation_when_key_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(401, json={"error": "invalid"})

        original_init = GovernanceClient.__init__

        def init_with_mock_transport(
            self: GovernanceClient, api_url: str, config: GovernanceConfig
        ) -> None:
            original_init(self, api_url, config)
            self._http.close()
            self._http = httpx.Client(
                transport=httpx.MockTransport(handler), timeout=config.api_timeout
            )

        monkeypatch.setattr(GovernanceClient, "__init__", init_with_mock_transport)
        monkeypatch.setenv("OPENBOX_URL", API_URL)
        monkeypatch.setenv("TEST_AGENT_API_KEY", "obx_test_bogus_key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-tests")

        agent = OpenBoxAgent(
            role="researcher",
            goal="Research things",
            backstory="Expert researcher",
            env_prefix="TEST_AGENT",
        )

        with create_openbox_engine() as engine:
            with pytest.raises(OpenBoxAuthError):
                engine.govern(Crew(name="test_crew", agents=[agent], tasks=[]))

        assert requests == [f"{API_URL}{AUTH_VALIDATE_PATH}"]

    def test_allows_crew_creation_when_key_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        original_init = GovernanceClient.__init__

        def init_with_mock_transport(
            self: GovernanceClient, api_url: str, config: GovernanceConfig
        ) -> None:
            original_init(self, api_url, config)
            self._http.close()
            self._http = httpx.Client(
                transport=httpx.MockTransport(handler), timeout=config.api_timeout
            )

        monkeypatch.setattr(GovernanceClient, "__init__", init_with_mock_transport)
        monkeypatch.setenv("OPENBOX_URL", API_URL)
        monkeypatch.setenv("TEST_AGENT_API_KEY", "obx_test_good_key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-tests")

        agent = OpenBoxAgent(
            role="researcher",
            goal="Research things",
            backstory="Expert researcher",
            env_prefix="TEST_AGENT",
        )

        with create_openbox_engine() as engine:
            crew = engine.govern(Crew(name="test_crew", agents=[agent], tasks=[]))
            assert crew is not None
            assert requests == [f"{API_URL}{AUTH_VALIDATE_PATH}"]
