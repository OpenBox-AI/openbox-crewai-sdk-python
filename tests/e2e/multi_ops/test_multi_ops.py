"""E2E tests for multi-operation governance (LLM + file I/O + HTTP).

Verifies that a single agent with file-read and HTTP tools exercises all
three governance layers in one crew run, with ``instrument_file_io=True``.

Prerequisites:
  - One OpenBox agent registered (researcher)
  - API key set in .env (see .env.example)

Usage:
    python3 -m tests.e2e.multi_ops.test_multi_ops
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from crewai import LLM, Crew, Process
from crewai.tools import tool
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

RESEARCHER_PREFIX = "OPENBOX_RESEARCHER"
SAMPLE_FILE = str(Path(__file__).parent / "sample_data.txt")


# -- Tools -------------------------------------------------------------------


@tool("Read Local File")
def read_file(file_path: str) -> str:
    """Read and return the contents of a local file."""
    return Path(file_path).read_text()


@tool("Fetch Webpage")
def fetch_webpage(url: str) -> str:
    """Fetch the text content of a webpage via HTTP GET."""
    import requests

    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text[:2000]


# -- Agent factory -----------------------------------------------------------


def _llm() -> LLM:
    return LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0)


def _make_researcher() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Researcher",
        env_prefix=RESEARCHER_PREFIX,
        goal="Gather information from multiple sources and produce a summary",
        backstory=(
            "You are a research analyst who gathers data from local files, "
            "the web, and your own knowledge to produce concise summaries."
        ),
        verbose=True,
        llm=_llm(),
        tools=[read_file, fetch_webpage],
    )


# -- Tests -------------------------------------------------------------------


def test_multi_ops_completes(caplog) -> None:
    """Agent with file + HTTP tools completes, exercising all 3 governance layers."""
    researcher = _make_researcher()

    task = OpenBoxTask(
        name="multi_source_research",
        activity_type="research",
        description=(
            f"1. Read the local file at {SAMPLE_FILE} to learn about the project.\n"
            "2. Fetch the webpage at https://httpbin.org/get to demonstrate HTTP governance.\n"
            "3. Using the information gathered, write a short 3-sentence summary of what "
            "OpenBox does and what data you retrieved from the webpage."
        ),
        expected_output=(
            "A 3-sentence summary combining information from the local file and the HTTP response."
        ),
        agent=researcher,
    )

    with create_openbox_engine(instrument_file_io=True, debug_log=True) as engine:
        crew = Crew(
            name="multi-ops-test",
            agents=[researcher],
            tasks=[task],
            process=Process.sequential,
        )

        with caplog.at_level(logging.WARNING, logger="openbox"):
            governed_crew = engine.govern(crew)
            result = governed_crew.kickoff()

        assert result is not None


# -- Main --------------------------------------------------------------------


def main() -> None:
    tests = [
        ("multi_ops_completes", test_multi_ops_completes),
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
    print("  MULTI-OPS GOVERNANCE TEST SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
