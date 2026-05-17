"""Self-handoff guard — Handoff payloads must not be emitted when the
origin DID matches the receiving agent's own DID.

Scenario: when a BLOCKed agent (or any agent that retries within its own
session) re-enters execute_task, _handoff_origin_did_var is still set to
its own DID from the outer call. The guard prevents emitting a phantom
self-handoff row that core would persist in session_handoffs.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from openbox.core.aip_signing import AgentIdentity
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import _multi_agent_session_id_var

from ..conftest import (
    API_KEY,
    CREW_EXEC_ID,
    CREW_NAME,
    MULTI_AGENT_SESSION_ID,
    make_agent,
    make_client_mock,
    make_config,
)


def _setup_agent(*, did: str | None = "did:aip:00000000-0000-5000-8000-000000000001"):
    client = make_client_mock()
    config = make_config()
    sp = GovernanceSpanProcessor(client, config)
    agent = make_agent()
    with patch.dict(os.environ, {"AGENT_API_KEY": API_KEY}):
        agent.configure_governance(client, sp, config, CREW_NAME, CREW_EXEC_ID)
    if did is None:
        agent._openbox_identity = None  # type: ignore[assignment]
    else:
        agent._openbox_identity = AgentIdentity(did=did, private_key="x" * 44)
    return agent


@pytest.fixture(autouse=True)
def _reset_multi_agent_session():
    """Each test sets its own session id; reset between tests."""
    token = _multi_agent_session_id_var.set(None)
    try:
        yield
    finally:
        _multi_agent_session_id_var.reset(token)


class TestShouldEmitHandoff:
    def test_skips_when_origin_did_matches_self(self) -> None:
        # The bug: same agent retries; contextvar carries its own DID.
        agent = _setup_agent(did="did:aip:00000000-0000-5000-8000-000000000001")
        _multi_agent_session_id_var.set(MULTI_AGENT_SESSION_ID)
        assert (
            agent._should_emit_handoff(
                "did:aip:00000000-0000-5000-8000-000000000001"
            )
            is False
        )

    def test_emits_when_origin_did_differs(self) -> None:
        agent = _setup_agent(did="did:aip:00000000-0000-5000-8000-000000000001")
        _multi_agent_session_id_var.set(MULTI_AGENT_SESSION_ID)
        assert (
            agent._should_emit_handoff(
                "did:aip:00000000-0000-5000-8000-000000000002"
            )
            is True
        )

    def test_skips_when_origin_did_is_none(self) -> None:
        agent = _setup_agent()
        _multi_agent_session_id_var.set(MULTI_AGENT_SESSION_ID)
        assert agent._should_emit_handoff(None) is False

    def test_skips_when_origin_did_is_empty(self) -> None:
        agent = _setup_agent()
        _multi_agent_session_id_var.set(MULTI_AGENT_SESSION_ID)
        assert agent._should_emit_handoff("") is False

    def test_skips_when_identity_is_unset(self) -> None:
        agent = _setup_agent(did=None)
        _multi_agent_session_id_var.set(MULTI_AGENT_SESSION_ID)
        assert (
            agent._should_emit_handoff(
                "did:aip:00000000-0000-5000-8000-000000000002"
            )
            is False
        )

    def test_skips_when_no_multi_agent_session(self) -> None:
        # Single-agent crew — no multi-agent session id set.
        agent = _setup_agent(did="did:aip:00000000-0000-5000-8000-000000000001")
        # _reset_multi_agent_session fixture already cleared it.
        assert (
            agent._should_emit_handoff(
                "did:aip:00000000-0000-5000-8000-000000000002"
            )
            is False
        )
