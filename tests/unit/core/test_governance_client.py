"""Tests for GovernanceClient.evaluate's on_api_error contract."""

from __future__ import annotations

import httpx
import pytest

from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.errors import GovernanceAPIError
from openbox.core.types import Verdict

API_URL = "https://api.openbox.ai"
API_KEY = "obx_test_key"
PAYLOAD = {
    "event_type": "ActivityStarted",
    "workflow_id": "wf-1",
    "run_id": "run-1",
    "activity_id": "act-1",
    "activity_type": "t",
    "task_queue": "q",
    "workflow_type": "T",
    "spans": [],
    "timestamp": "2026-04-24T00:00:00Z",
    "agent_role": "r",
    "metadata": {},
}


def _client_with_transport(
    handler: httpx.MockTransport, *, on_api_error: str
) -> GovernanceClient:
    config = GovernanceConfig(on_api_error=on_api_error)
    client = GovernanceClient(API_URL, config)
    client._http.close()
    client._http = httpx.Client(transport=handler, timeout=client._config.api_timeout)
    return client


class TestOnApiErrorPolicy:
    def test_fail_open_returns_allow_on_connection_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _client_with_transport(
            httpx.MockTransport(handler), on_api_error="fail_open"
        )
        try:
            response = client.evaluate(PAYLOAD, API_KEY)
            assert response.verdict == Verdict.ALLOW
            assert response.fallback_used is True
        finally:
            client.close()

    def test_fail_closed_raises_on_connection_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _client_with_transport(
            httpx.MockTransport(handler), on_api_error="fail_closed"
        )
        try:
            with pytest.raises(GovernanceAPIError):
                client.evaluate(PAYLOAD, API_KEY)
        finally:
            client.close()

    def test_fail_open_returns_allow_on_5xx(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal server error")

        client = _client_with_transport(
            httpx.MockTransport(handler), on_api_error="fail_open"
        )
        try:
            response = client.evaluate(PAYLOAD, API_KEY)
            assert response.verdict == Verdict.ALLOW
            assert response.fallback_used is True
        finally:
            client.close()

    def test_fail_closed_raises_on_5xx(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="service unavailable")

        client = _client_with_transport(
            httpx.MockTransport(handler), on_api_error="fail_closed"
        )
        try:
            with pytest.raises(GovernanceAPIError):
                client.evaluate(PAYLOAD, API_KEY)
        finally:
            client.close()

    def test_default_policy_is_fail_open(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = GovernanceClient(API_URL, GovernanceConfig())
        client._http.close()
        client._http = httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=client._config.api_timeout,
        )
        try:
            response = client.evaluate(PAYLOAD, API_KEY)
            assert response.verdict == Verdict.ALLOW
            assert response.fallback_used is True
        finally:
            client.close()
