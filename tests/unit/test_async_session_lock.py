"""Async session bootstrap must not block the event loop on a threading.Lock."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_concurrent__aensure_session_calls_evaluate_once() -> None:

    from tests.unit.conftest import make_agent

    agent = make_agent(env_prefix="OPENBOX_TEST")
    agent._openbox_api_key = "obx_test_aaaaaaaaaaaaaaaaaaaaaaaa"
    client = AsyncMock()
    client.aevaluate = AsyncMock()

    metadata: dict[str, Any] = {"crew_name": "c", "crew_execution_id": "x"}
    await asyncio.gather(
        agent._aensure_session(client, "c", metadata),
        agent._aensure_session(client, "c", metadata),
        agent._aensure_session(client, "c", metadata),
        agent._aensure_session(client, "c", metadata),
        agent._aensure_session(client, "c", metadata),
    )
    assert client.aevaluate.call_count == 1


@pytest.mark.asyncio
async def test__aensure_session_uses_asyncio_lock() -> None:

    from tests.unit.conftest import make_agent

    agent = make_agent(env_prefix="OPENBOX_TEST")
    agent._openbox_api_key = "obx_test_aaaaaaaaaaaaaaaaaaaaaaaa"
    client = AsyncMock()
    client.aevaluate = AsyncMock()
    await agent._aensure_session(client, "c", {"crew_name": "c", "crew_execution_id": "x"})

    # After first call, the async lock should have been instantiated as asyncio.Lock
    # so that subsequent concurrent calls await rather than block the event loop.
    lock = getattr(agent, "_asession_lock", None)
    assert isinstance(lock, asyncio.Lock), (
        f"async session path must use asyncio.Lock, got {type(lock).__name__}"
    )
