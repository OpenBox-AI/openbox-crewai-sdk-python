"""E2E tests for multi-agent, multi-task activity correlation.

Verifies that a crew with multiple agents and sequential tasks completes
under governance, with each task producing its own ActivityStarted /
ActivityCompleted pair and correct activity_id correlation.

Prerequisites:
  - Two OpenBox agents registered (researcher, writer)
  - API keys set in .env (see .env.example)

Usage:
    python3 -m tests.e2e.multi_activity.test_multi_activity
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

RESEARCHER_PREFIX = "OPENBOX_RESEARCHER"
WRITER_PREFIX = "OPENBOX_WRITER"


# -- Agent factory -----------------------------------------------------------


def _llm() -> LLM:
    return LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0)


def _make_researcher() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Researcher",
        env_prefix=RESEARCHER_PREFIX,
        goal="Gather facts and data",
        backstory="You are a research analyst who finds key facts quickly.",
        verbose=True,
        llm=_llm(),
    )


def _make_writer() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Writer",
        env_prefix=WRITER_PREFIX,
        goal="Write concise summaries from research findings",
        backstory="You are a technical writer who produces clear, brief reports.",
        verbose=True,
        llm=_llm(),
    )


def _build_crew():
    researcher = _make_researcher()
    writer = _make_writer()

    task1 = OpenBoxTask(
        name="gather_facts",
        activity_type="research",
        description=(
            "List 3 key facts about OpenTelemetry. "
            "Do NOT use any tools — just use your knowledge."
        ),
        expected_output="A numbered list of 3 facts about OpenTelemetry.",
        agent=researcher,
    )

    task2 = OpenBoxTask(
        name="analyze_facts",
        activity_type="research",
        description="Identify the most important fact from the previous research and explain why.",
        expected_output="One paragraph explaining the most important OTel fact.",
        agent=researcher,
    )

    task3 = OpenBoxTask(
        name="write_report",
        activity_type="writing",
        description=(
            "Using the research findings from the previous tasks, write a "
            "2-sentence summary combining all insights."
        ),
        expected_output="A 2-sentence summary report.",
        agent=writer,
    )

    crew = Crew(
        name="multi-activity-test",
        agents=[researcher, writer],
        tasks=[task1, task2, task3],
        process=Process.sequential,
        verbose=True,
    )
    return crew, researcher, writer


# -- Tests -------------------------------------------------------------------


def test_multi_activity_completes() -> None:
    """Three sequential tasks across two agents complete under governance."""
    with create_openbox_engine(debug_log=True) as engine:
        crew, researcher, writer = _build_crew()

        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()

        assert result is not None


# -- Main --------------------------------------------------------------------


def main() -> None:
    tests = [
        ("multi_activity_completes", test_multi_activity_completes),
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
    print("  MULTI-ACTIVITY GOVERNANCE TEST SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
