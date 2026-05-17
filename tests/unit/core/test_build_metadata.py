"""Test build_metadata utility function.

multi_agent_session_id was moved out of metadata onto a top-level field on
the payloads themselves, so build_metadata no longer carries it.
"""

from __future__ import annotations

from openbox.utils import build_metadata

from ..conftest import CREW_EXEC_ID, CREW_NAME, make_agent_context


class TestBuildMetadata:
    def test_includes_crew_name_and_crew_execution_id(self) -> None:
        ctx = make_agent_context()
        meta = build_metadata(ctx)
        assert meta["crew_name"] == CREW_NAME
        assert meta["crew_execution_id"] == CREW_EXEC_ID

    def test_omits_multi_agent_session_id(self) -> None:
        # multi_agent_session_id is no longer a metadata key — it's a
        # top-level field on the payload, populated by the ContextVar.
        ctx = make_agent_context(multi_agent_session_id="anything")
        meta = build_metadata(ctx)
        assert "multi_agent_session_id" not in meta
