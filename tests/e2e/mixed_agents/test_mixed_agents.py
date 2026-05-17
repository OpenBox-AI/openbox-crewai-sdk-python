"""E2E tests for mixed governed + ungoverned agents in the same crew.

Verifies that a GovernedCrew can contain both an OpenBoxAgent (governed)
and a standard CrewAI Agent (ungoverned) without errors.

Prerequisites:
  - One OpenBox agent registered (senior_researcher)
  - API key set in .env (see .env.example)

Usage:
    python3 -m tests.e2e.mixed_agents.test_mixed_agents
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from crewai import LLM, Agent, Crew, Process
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

GOVERNED_PREFIX = "OPENBOX_SENIOR_RESEARCHER"


# -- Agent factory -----------------------------------------------------------


def _llm() -> LLM:
    return LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0)


def _make_governed_agent() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Senior Researcher",
        env_prefix=GOVERNED_PREFIX,
        goal="Research AI safety topics",
        backstory="You are an expert AI researcher.",
        verbose=True,
        llm=_llm(),
    )


def _make_ungoverned_agent() -> Agent:
    return Agent(
        role="Helper",
        goal="Format and clean up text",
        backstory="You are a helpful text formatter.",
        verbose=True,
        llm=_llm(),
    )


# -- Tests -------------------------------------------------------------------


def test_mixed_agents_complete() -> None:
    """Governed OpenBoxAgent + ungoverned Agent coexist and complete."""
    governed = _make_governed_agent()
    ungoverned = _make_ungoverned_agent()

    task1 = OpenBoxTask(
        name="research_topic",
        activity_type="research",
        description="Research the topic: AI safety. Provide 3 key points.",
        expected_output="Detailed research findings",
        agent=governed,
    )

    from crewai import Task

    task2 = Task(
        description="Format the research findings into bullet points",
        expected_output="Formatted bullet point summary",
        agent=ungoverned,
    )

    with create_openbox_engine(debug_log=True) as engine:
        crew = Crew(
            name="mixed-agents-test",
            agents=[governed, ungoverned],
            tasks=[task1, task2],
            process=Process.sequential,
            verbose=True,
        )

        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()

        assert result is not None


# -- Main --------------------------------------------------------------------


def main() -> None:
    tests = [
        ("mixed_agents_complete", test_mixed_agents_complete),
    ]

    results: dict[str, bool] = {}
    for name, fn in tests:
        try:
            fn()
            results[name] = True
        except Exception as e:
            log.error("%s failed: %s", name, e)
            results[name] = False

    print(f"\n{'=' * 60}")
    print("  MIXED AGENTS GOVERNANCE TEST SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
