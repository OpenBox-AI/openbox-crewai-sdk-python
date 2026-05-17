"""Verify crew_execution_id is generated and passed to agents on kickoff."""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

from openbox.crewai.crew import GovernedCrew
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import _multi_agent_session_id_var

from ..conftest import (
    API_KEY,
    CREW_NAME,
    MULTI_AGENT_SESSION_ID,
    make_agent,
    make_client_mock,
    make_config,
)


class TestCrewKickoffMetadata:
    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_kickoff_generates_crew_execution_id(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(
            name=CREW_NAME,
            agents=[agent],
            tasks=[],
        )
        crew.configure_governance(client, config, sp)

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "kickoff", return_value="done"),
        ):
            crew.kickoff(engine=object())

        assert crew._crew_execution_id
        uuid.UUID(crew._crew_execution_id)

        assert agent._crew_execution_id == crew._crew_execution_id
        assert agent._crew_name == CREW_NAME

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_kickoff_inside_flow_inherits_flow_session_id(self) -> None:
        """Outer wins: a Crew inside a Flow uses the Flow's session id, not its own."""
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(
            name=CREW_NAME,
            agents=[agent],
            tasks=[],
        )
        crew.configure_governance(client, config, sp)

        captured: dict[str, str | None] = {}

        def _capture(*_args: object, **_kwargs: object) -> str:
            captured["session_id"] = _multi_agent_session_id_var.get()
            return "done"

        token = _multi_agent_session_id_var.set(MULTI_AGENT_SESSION_ID)
        try:
            with (
                patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
                patch.object(
                    GovernedCrew.__bases__[0], "kickoff", side_effect=_capture
                ),
            ):
                crew.kickoff(engine=object())
        finally:
            _multi_agent_session_id_var.reset(token)

        assert captured["session_id"] == MULTI_AGENT_SESSION_ID

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_standalone_kickoff_opens_own_session_and_resets(self) -> None:
        """A standalone Crew opens its own session id and resets it after."""
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        captured: dict[str, str | None] = {}

        def _capture(*_args: object, **_kwargs: object) -> str:
            captured["session_id"] = _multi_agent_session_id_var.get()
            return "done"

        assert _multi_agent_session_id_var.get() is None

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "kickoff", side_effect=_capture),
        ):
            crew.kickoff(engine=object())

        assert captured["session_id"] is not None
        uuid.UUID(captured["session_id"])
        assert _multi_agent_session_id_var.get() is None

    @patch.dict(os.environ, {"AGENT_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-fake"})
    def test_second_kickoff_gets_fresh_crew_execution_id(self) -> None:
        client = make_client_mock()
        config = make_config()
        sp = GovernanceSpanProcessor(client, config)
        agent = make_agent()

        crew = GovernedCrew(name=CREW_NAME, agents=[agent], tasks=[])
        crew.configure_governance(client, config, sp)

        with (
            patch.object(GovernedCrew, "bind_openbox_engine", return_value=crew),
            patch.object(GovernedCrew.__bases__[0], "kickoff", return_value="done"),
        ):
            crew.kickoff(engine=object())
            first_exec_id = crew._crew_execution_id
            crew.kickoff(engine=object())

        assert crew._crew_execution_id != first_exec_id
