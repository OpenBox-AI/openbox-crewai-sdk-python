"""E2E tests for hierarchical process governance.

Verifies that a crew using ``Process.hierarchical`` with a manager agent
completes under governance. The manager delegates tasks to worker agents.

Prerequisites:
  - Three OpenBox agents registered (senior_researcher, researcher, data_analyst)
  - API keys set in .env (see .env.example)

Usage:
    python3 -m tests.e2e.hierarchical.test_hierarchical
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from crewai import LLM, Crew, Process
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

MANAGER_PREFIX = "OPENBOX_SENIOR_RESEARCHER"
RESEARCHER_PREFIX = "OPENBOX_RESEARCHER"
ANALYST_PREFIX = "OPENBOX_DATA_ANALYST"


# -- Agent factory -----------------------------------------------------------


def _llm() -> LLM:
    return LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0)


def _make_manager() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Senior Researcher",
        env_prefix=MANAGER_PREFIX,
        goal="Coordinate research across topics and synthesize findings",
        backstory="You are a senior research director who delegates to specialists.",
        allow_delegation=True,
        verbose=True,
        llm=_llm(),
    )


def _make_researcher() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Researcher",
        env_prefix=RESEARCHER_PREFIX,
        goal="Research assigned topics thoroughly",
        backstory="You are a research analyst.",
        verbose=True,
        llm=_llm(),
    )


def _make_analyst() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Data Analyst",
        env_prefix=ANALYST_PREFIX,
        goal="Analyze data and extract insights",
        backstory="You are a data analyst who identifies patterns and key takeaways.",
        verbose=True,
        llm=_llm(),
    )


# -- Tests -------------------------------------------------------------------


def test_hierarchical_completes() -> None:
    """Hierarchical crew with manager + 2 workers completes under governance."""
    manager = _make_manager()
    researcher = _make_researcher()
    analyst = _make_analyst()

    task1 = OpenBoxTask(
        name="ai_safety_research",
        activity_type="research",
        description="Research the latest developments in AI safety",
        expected_output="Summary of AI safety developments",
    )

    task2 = OpenBoxTask(
        name="synthesize_report",
        activity_type="writing",
        description="Synthesize the AI safety research into a short report",
        expected_output="A concise research report",
    )

    with create_openbox_engine(debug_log=True) as engine:
        crew = Crew(
            name="hierarchical-test",
            agents=[researcher, analyst],
            tasks=[task1, task2],
            process=Process.hierarchical,
            manager_agent=manager,
            verbose=True,
        )

        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()

        assert result is not None


# -- Main --------------------------------------------------------------------


def main() -> None:
    tests = [
        ("hierarchical_completes", test_hierarchical_completes),
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
    print("  HIERARCHICAL GOVERNANCE TEST SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
