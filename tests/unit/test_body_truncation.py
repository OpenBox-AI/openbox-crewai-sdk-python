"""Tests for max_body_size enforcement at body capture."""

from __future__ import annotations

from openbox.core.config import GovernanceConfig
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import truncate_body


def test_truncate_body_under_limit_passthrough() -> None:
    assert truncate_body("hello", 1024) == "hello"


def test_truncate_body_at_limit_passthrough() -> None:
    assert truncate_body("a" * 100, 100) == "a" * 100


def test_truncate_body_over_limit_truncated_with_marker() -> None:
    out = truncate_body("a" * 200, 100)
    assert out is not None
    assert out.startswith("a" * 100)
    assert "truncated" in out.lower()


def test_truncate_body_none_passthrough() -> None:
    assert truncate_body(None, 100) is None


def test_truncate_body_no_limit_passthrough() -> None:
    assert truncate_body("a" * 10000, None) == "a" * 10000


def test_span_processor_truncates_str_body_when_max_set() -> None:
    config = GovernanceConfig()
    config.max_body_size = 50
    sp = GovernanceSpanProcessor(
        governance_client=object(),  # type: ignore[arg-type]
        config=config,
    )
    sp.store_body(span_id=1, key="request_body", value="x" * 200)
    stored = sp.get_body(span_id=1)
    assert stored is not None
    assert len(stored["request_body"]) < 200
    assert stored["request_body"].startswith("x" * 50)


def test_span_processor_passthrough_when_no_limit() -> None:
    config = GovernanceConfig()
    config.max_body_size = None
    sp = GovernanceSpanProcessor(
        governance_client=object(),  # type: ignore[arg-type]
        config=config,
    )
    huge = "x" * 10_000
    sp.store_body(span_id=2, key="request_body", value=huge)
    stored = sp.get_body(span_id=2)
    assert stored is not None
    assert stored["request_body"] == huge


def test_span_processor_does_not_truncate_dict_values() -> None:
    config = GovernanceConfig()
    config.max_body_size = 10
    sp = GovernanceSpanProcessor(
        governance_client=object(),  # type: ignore[arg-type]
        config=config,
    )
    headers = {"Content-Type": "application/json", "X-Long": "x" * 200}
    sp.store_body(span_id=3, key="request_headers", value=headers)
    stored = sp.get_body(span_id=3)
    assert stored is not None
    assert stored["request_headers"] == headers
