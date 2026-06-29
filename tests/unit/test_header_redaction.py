"""Tests for sensitive-header redaction in captured HTTP payloads."""

from __future__ import annotations

import pytest

from openbox.utils import REDACTED_VALUE, redact_sensitive_headers


def test_authorization_redacted() -> None:
    out = redact_sensitive_headers({"Authorization": "Bearer secret"})
    assert out == {"Authorization": REDACTED_VALUE}


def test_cookie_and_set_cookie_redacted() -> None:
    out = redact_sensitive_headers(
        {"Cookie": "session=abc", "Set-Cookie": "session=def; HttpOnly"}
    )
    assert out["Cookie"] == REDACTED_VALUE
    assert out["Set-Cookie"] == REDACTED_VALUE


def test_proxy_authorization_redacted() -> None:
    assert redact_sensitive_headers({"Proxy-Authorization": "Basic xx"}) == {
        "Proxy-Authorization": REDACTED_VALUE
    }


@pytest.mark.parametrize(
    "header_name",
    ["X-Api-Key", "X-API-KEY", "x-api-key", "X-Auth-Token"],
)
def test_api_key_headers_redacted_case_insensitive(header_name: str) -> None:
    out = redact_sensitive_headers({header_name: "secret-value"})
    assert out[header_name] == REDACTED_VALUE


def test_non_sensitive_headers_passthrough() -> None:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "openbox-sdk/1.0",
        "X-Request-Id": "abc-123",
    }
    assert redact_sensitive_headers(headers) == headers


def test_mixed_headers() -> None:
    out = redact_sensitive_headers(
        {
            "Authorization": "Bearer s",
            "Content-Type": "application/json",
            "Cookie": "k=v",
        }
    )
    assert out["Authorization"] == REDACTED_VALUE
    assert out["Cookie"] == REDACTED_VALUE
    assert out["Content-Type"] == "application/json"


def test_none_returns_none() -> None:
    assert redact_sensitive_headers(None) is None


def test_empty_returns_empty() -> None:
    assert redact_sensitive_headers({}) == {}


def test_does_not_mutate_input() -> None:
    original = {"Authorization": "Bearer secret"}
    redact_sensitive_headers(original)
    assert original == {"Authorization": "Bearer secret"}


def test_http_span_data_uses_redactor() -> None:
    import inspect

    from openbox.core.spans import http as http_module

    src = inspect.getsource(http_module.HttpSpanData.from_otel_span)
    assert "redact_sensitive_headers" in src
