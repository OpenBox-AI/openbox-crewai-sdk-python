"""Verify multi_agent_session_id is shared across crews in a Flow."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from openbox import create_openbox_flow
from openbox.crewai.agent import OpenBoxAgent
from openbox.crewai.crew import GovernedCrew
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import _multi_agent_session_id_var, build_metadata

from ..conftest import (
    API_KEY,
    MULTI_AGENT_SESSION_ID,
    make_agent,
    make_agent_context,
    make_client_mock,
    make_config,
)


class TestFlowCorrelation:
    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_multiple_crews_share_multi_agent_session_id(self) -> None:
        """When multiple crews run inside a flow, they see the same multi_agent_session_id."""
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)

        agent1 = make_agent()
        agent2 = OpenBoxAgent(
            role="writer",
            goal="Write things",
            backstory="Expert writer",
            env_prefix="AGENT",
        )

        crew1 = GovernedCrew(name="crew_alpha", agents=[agent1], tasks=[])
        crew1.configure_governance(client, config, sp)

        crew2 = GovernedCrew(name="crew_beta", agents=[agent2], tasks=[])
        crew2.configure_governance(client, config, sp)

        token = _multi_agent_session_id_var.set(MULTI_AGENT_SESSION_ID)
        try:
            with (
                patch.object(GovernedCrew, "bind_openbox_engine", side_effect=[crew1, crew2]),
                                patch.object(GovernedCrew.__bases__[0], "kickoff", return_value="done"),
            ):
                crew1.kickoff(engine=object())
                crew2.kickoff(engine=object())
        finally:
            _multi_agent_session_id_var.reset(token)

        token2 = _multi_agent_session_id_var.set(MULTI_AGENT_SESSION_ID)
        try:
            metadata1: dict[str, Any] = {
                "crew_name": agent1._crew_name,
                "crew_execution_id": agent1._crew_execution_id,
            }
            flow_id1 = _multi_agent_session_id_var.get()
            if flow_id1:
                metadata1["multi_agent_session_id"] = flow_id1

            metadata2: dict[str, Any] = {
                "crew_name": agent2._crew_name,
                "crew_execution_id": agent2._crew_execution_id,
            }
            flow_id2 = _multi_agent_session_id_var.get()
            if flow_id2:
                metadata2["multi_agent_session_id"] = flow_id2
        finally:
            _multi_agent_session_id_var.reset(token2)

        assert metadata1["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID
        assert metadata2["multi_agent_session_id"] == MULTI_AGENT_SESSION_ID
        assert metadata1["multi_agent_session_id"] == metadata2["multi_agent_session_id"]

        assert metadata1["crew_name"] == "crew_alpha"
        assert metadata2["crew_name"] == "crew_beta"
        assert metadata1["crew_execution_id"] != metadata2["crew_execution_id"]

    def test_multi_agent_session_id_not_set_outside_flow(self) -> None:
        token = _multi_agent_session_id_var.set(None)
        try:
            flow_id = _multi_agent_session_id_var.get()
            assert flow_id is None

            ctx = make_agent_context(multi_agent_session_id=flow_id)
            meta = build_metadata(ctx)
            assert "multi_agent_session_id" not in meta
        finally:
            _multi_agent_session_id_var.reset(token)

    def test_create_openbox_flow_sets_contextvar(self) -> None:
        @dataclass
        class FakeFlow:
            def kickoff(self, **kwargs: Any) -> str:
                flow_id = _multi_agent_session_id_var.get()
                assert flow_id is not None
                uuid.UUID(flow_id)
                return flow_id  # type: ignore[return-value]

        flow = create_openbox_flow(FakeFlow)
        result = flow.kickoff()

        assert _multi_agent_session_id_var.get() is None
        uuid.UUID(result)
