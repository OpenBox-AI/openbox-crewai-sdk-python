"""E2E tests for OIAM AI-inventory resource permissions on outbound HTTP.

Core matches each captured ``http_url`` span against the org's active
``ai_inventory_resources`` and applies the agent's effective allow/deny for the
matched resource.

The Researcher agent holds two enforceable denies keyed on ``http_url``
(``httpbin.org``, ``postman-echo.com``) and no grant covering
``example.com``, so the same tool blocks or passes purely on the URL it is
handed.

Prerequisites:

1. Credentials in .env as OPENBOX_RESEARCHER_* (see .env.example), pointing at
   the ``CrewAI E2E - Researcher`` agent.

2. Three active rows in ``ai_inventory_resources`` for the agent's org, each
   matching on ``http_url``::

     HTTPBin Test Endpoint   http_url contains httpbin.org        enforceable
     Postman Echo Endpoint   http_url contains postman-echo.com   enforceable

   and no resource matching ``example.com``.

3. An effective ``deny`` for the Researcher on both of the above — granted
   directly or via role/group; confirm with::

     SELECT r.name, ea.effect, r.enforceable
       FROM oiam_agent_resource_permission_effective_access ea
       JOIN ai_inventory_resources r ON r.id::text = ea.resource_id::text
      WHERE ea.agent_id::text = '<researcher agent id>';

4. Org runtime mode ``enforced`` in ``oiam_runtime_configurations`` — under
   ``observe`` a deny is recorded but does not block, and every deny case here
   fails.

Usage:
    uv run pytest tests/e2e/resource_permissions/test_resource_permissions.py
"""

import logging
import os
from pathlib import Path

import pytest
import requests
from crewai import LLM, Crew, Process
from crewai.tools import tool
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine
from openbox.core.errors import GovernanceBlockedError

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ENV_PREFIX = "OPENBOX_RESEARCHER"

DENIED_HTTPBIN = "https://httpbin.org/get"
DENIED_POSTMAN = "https://postman-echo.com/get"
UNGRANTED_HOST = "https://example.com"


@tool("Fetch URL")
def fetch_url(url: str) -> str:
    """Fetch a URL over HTTPS and return a short description of the response."""
    response = requests.get(url, timeout=15)
    return f"{url} -> HTTP {response.status_code}, {len(response.content)} bytes"


def _make_agent(role: str) -> OpenBoxAgent:
    return OpenBoxAgent(
        role=role,
        env_prefix=ENV_PREFIX,
        goal="Fetch exactly the URLs the task names, using the Fetch URL tool.",
        backstory="Market intelligence researcher that gathers data from web APIs.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
        tools=[fetch_url],
    )


def _run(crew_name: str, task: OpenBoxTask, agent: OpenBoxAgent):
    with create_openbox_engine(debug_log=True) as engine:
        crew = Crew(
            name=crew_name,
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )
        return engine.govern(crew).kickoff()


def test_ungranted_host_allowed() -> None:
    """A URL matching no inventory resource carries no deny and proceeds."""
    agent = _make_agent("Resource Permission Allow Tester")
    task = OpenBoxTask(
        name="resource_permission_allowed",
        activity_type="http_request",
        description=(
            f"Use the Fetch URL tool exactly once on {UNGRANTED_HOST} and "
            "report the HTTP status code you received."
        ),
        expected_output="The HTTP status code.",
        agent=agent,
    )

    result = _run("resource-permission-allow", task, agent)
    assert result is not None


@pytest.mark.parametrize(
    ("label", "url"),
    [
        ("httpbin", DENIED_HTTPBIN),
        ("postman_echo", DENIED_POSTMAN),
    ],
)
def test_denied_resource_blocks(label: str, url: str) -> None:
    """Each enforceable deny keyed on http_url blocks its own host."""
    agent = _make_agent("Resource Permission Deny Tester")
    task = OpenBoxTask(
        name=f"resource_permission_denied_{label}",
        activity_type="http_request",
        description=(
            f"Use the Fetch URL tool exactly once on {url} and report the "
            "HTTP status code you received."
        ),
        expected_output="The HTTP status code.",
        agent=agent,
    )

    with pytest.raises(GovernanceBlockedError):
        _run(f"resource-permission-deny-{label}", task, agent)


def test_multiple_calls_block_on_first_denied_resource() -> None:
    """A run mixing an ungranted host with denied ones still blocks.

    The ungranted call is ordered first so the block cannot be attributed to
    the agent being denied outright — only the denied URL can produce it.
    """
    agent = _make_agent("Resource Permission Multi Tester")
    task = OpenBoxTask(
        name="resource_permission_multi",
        activity_type="http_request",
        description=(
            "Use the Fetch URL tool once per URL, in this order: "
            f"1. {UNGRANTED_HOST} "
            f"2. {DENIED_HTTPBIN} "
            f"3. {DENIED_POSTMAN} "
            "Report each HTTP status code."
        ),
        expected_output="Three HTTP status codes.",
        agent=agent,
    )

    with pytest.raises(GovernanceBlockedError):
        _run("resource-permission-multi", task, agent)
