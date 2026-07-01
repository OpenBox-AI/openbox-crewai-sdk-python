"""PROD-244: governed activity is attributed to the agent in the execution frame.

The execution frame (a ContextVar set around each agent's task) nests and
restores through delegation, so a coworker's frame is popped when it returns and
the delegating agent's frame is active again. Completed-stage evaluation resolves
the acting agent solely from that frame; with no frame there is no governed agent
executing, so evaluation is skipped rather than attributed to anyone.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

from openbox.core.spans import HttpSpanData, Stage
from openbox.core.types import AgentContext
from openbox.crewai.agent import OpenBoxAgent
from openbox.crewai.task import OpenBoxTask
from openbox.instrumentation.interceptors._runtime import (
    configure,
    evaluate_completed,
    reset,
)
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import (
    get_current_execution_frame,
    reset_current_execution_frame,
    set_current_execution_frame,
)

from ...conftest import ALLOW_RESPONSE, make_client_mock, make_config


def _ctx(role: str, api_key: str) -> AgentContext:
    return AgentContext(
        role=role,
        session_id=f"sess-{role}",
        run_id=f"run-{role}",
        api_key=api_key,
        crew_name="crew",
        crew_execution_id="exec-1",
    )


def _make_mock_span(trace_id: int, span_id: int) -> MagicMock:
    span = MagicMock()
    span_ctx = MagicMock()
    span_ctx.trace_id = trace_id
    span_ctx.span_id = span_id
    span.get_span_context.return_value = span_ctx
    span.name = "HTTP GET"
    span.kind = MagicMock(name="CLIENT")
    span.start_time = 1_000_000_000
    span.end_time = 2_000_000_000
    span.parent = None
    span.status = MagicMock()
    span.status.status_code = MagicMock(name="UNSET")
    span.status.description = None
    span.events = []
    span.attributes = {"http.method": "GET", "http.url": "https://example.com/api"}
    return span


def test_on_end_attributes_to_execution_frame() -> None:
    client = make_client_mock()
    sp = GovernanceSpanProcessor(client, make_config())
    manager = _ctx("manager", "obx_test_manager")

    token = set_current_execution_frame(
        manager, {"activity_id": "act-manager", "activity_type": "research"}
    )
    try:
        sp.on_end(_make_mock_span(0x55557, 0x77779))
    finally:
        reset_current_execution_frame(token)

    payload, api_key = client.evaluate.call_args[0][0], client.evaluate.call_args[0][1]
    assert payload["agent_role"] == "manager"
    assert payload["workflow_type"] == "manager Agent"
    assert payload["activity_id"] == "act-manager"
    assert api_key == "obx_test_manager"


def test_on_end_skips_without_frame() -> None:
    client = make_client_mock()
    sp = GovernanceSpanProcessor(client, make_config())

    sp.on_end(_make_mock_span(0x55558, 0x7777A))  # no frame set

    client.evaluate.assert_not_called()


def test_evaluate_completed_attributes_to_execution_frame() -> None:
    client = make_client_mock()
    config = make_config()
    sp = GovernanceSpanProcessor(client, config)
    manager = _ctx("manager", "obx_test_manager")

    configure(
        span_processor=sp,
        governance_client=client,
        config=config,
        ignored_url_prefixes=set(),
    )
    span_data = HttpSpanData(stage=Stage.COMPLETED, span_id="s1", trace_id="t1", name="HTTP GET")

    token = set_current_execution_frame(
        manager, {"activity_id": "act-manager", "activity_type": "research"}
    )
    try:
        evaluate_completed(0x66661, span_data)
    finally:
        reset_current_execution_frame(token)
        reset()

    payload, api_key = client.evaluate.call_args[0][0], client.evaluate.call_args[0][1]
    assert payload["agent_role"] == "manager"
    assert api_key == "obx_test_manager"


def test_evaluate_completed_skips_without_frame() -> None:
    client = make_client_mock()
    config = make_config()
    sp = GovernanceSpanProcessor(client, config)

    configure(
        span_processor=sp,
        governance_client=client,
        config=config,
        ignored_url_prefixes=set(),
    )
    span_data = HttpSpanData(stage=Stage.COMPLETED, span_id="s1", trace_id="t1", name="HTTP GET")
    try:
        evaluate_completed(0x66662, span_data)  # no frame set
    finally:
        reset()

    client.evaluate.assert_not_called()


async def test_parallel_agents_attribute_independently() -> None:
    """Two agents running concurrently must each attribute to themselves. The
    frame is a ContextVar, so each asyncio task gets an isolated copy — one
    agent setting its frame cannot bleed into another's concurrent operation
    (a shared trace-keyed slot would race here).
    """
    client = make_client_mock()
    sp = GovernanceSpanProcessor(client, make_config())

    recorded: list[tuple[str, str]] = []

    def _record(payload, api_key=None, identity=None):
        recorded.append((api_key, payload["agent_role"]))
        return ALLOW_RESPONSE

    client.evaluate.side_effect = _record

    async def run_agent(role: str, api_key: str) -> None:
        token = set_current_execution_frame(
            _ctx(role, api_key), {"activity_id": f"act-{role}", "activity_type": "task"}
        )
        try:
            # Yield so the other agent sets ITS frame before this one evaluates;
            # with a shared slot the last writer would win and misattribute.
            await asyncio.sleep(0)
            sp.on_end(_make_mock_span(0x1000, 0x2000))
        finally:
            reset_current_execution_frame(token)

    await asyncio.gather(
        run_agent("alice", "obx_test_alice"),
        run_agent("bob", "obx_test_bob"),
    )

    # Each agent's key is paired with its OWN role — no cross-contamination.
    assert sorted(recorded) == [
        ("obx_test_alice", "alice"),
        ("obx_test_bob", "bob"),
    ]


def test_real_nested_execute_task_restores_delegating_agent() -> None:
    """End-to-end write-side proof: drive the actual nested ``execute_task``
    frame set/reset (manager delegates to worker, worker returns) and assert the
    manager's post-delegation operation attributes to the manager — not the
    worker whose span shares the same trace.
    """
    client = make_client_mock()
    config = make_config()
    sp = GovernanceSpanProcessor(client, config)
    seen: dict[str, str] = {}

    def _attributed_role() -> str:
        # Trigger a completed governed operation in the *current* frame and read
        # back who it was attributed to.
        client.evaluate.reset_mock()
        sp.on_end(_make_mock_span(0xABC, 0xDEF))
        return client.evaluate.call_args[0][0]["agent_role"]

    env = {
        "OPENAI_API_KEY": "sk-fake",
        "MANAGER_API_KEY": "obx_test_manager",
        "WORKER_API_KEY": "obx_test_worker",
    }
    with patch.dict(os.environ, env):
        manager = OpenBoxAgent(role="manager", goal="g", backstory="b", env_prefix="MANAGER")
        worker = OpenBoxAgent(role="worker", goal="g", backstory="b", env_prefix="WORKER")
        manager.configure_governance(client, sp, config, "crew", "exec-1")
        worker.configure_governance(client, sp, config, "crew", "exec-1")

        worker_task = OpenBoxTask(
            description="w", expected_output="o", agent=worker, activity_type="task"
        )

        def base_execute(task, context=None, tools=None):  # replaces crewai.Agent.execute_task
            role = get_current_execution_frame().agent_context.role
            if role == "manager":
                seen["mgr_pre"] = _attributed_role()
                worker.execute_task(worker_task)  # delegation: nested, same trace
                seen["mgr_frame_post"] = get_current_execution_frame().agent_context.role
                seen["mgr_post"] = _attributed_role()
                return "manager done"
            seen["wkr"] = _attributed_role()
            return "worker done"

        manager_task = OpenBoxTask(
            description="m", expected_output="o", agent=manager, activity_type="task"
        )
        with patch("crewai.Agent.execute_task", side_effect=base_execute):
            manager.execute_task(manager_task)

    assert seen["mgr_pre"] == "manager"
    assert seen["wkr"] == "worker"
    # The fix: after the worker returns, the manager's frame is restored, so its
    # operation is attributed to the manager — not the worker.
    assert seen["mgr_frame_post"] == "manager"
    assert seen["mgr_post"] == "manager"
