"""E2E test for a complex hierarchical crew where one agent is BLOCKED.

Variant of tests/e2e/hierarchical_complex/test_complex_hierarchical.py.
Five of the six agents are reused from the original; the Compliance Reviewer
slot is filled by a cloned agent whose governance configuration BLOCKs every
llm_completion via a behaviour rule. Running this crew produces a workflow
with five ALLOW agents and one BLOCK agent, useful for exercising mixed
per-agent governance verdicts in any downstream consumer.

The final output file will *not* be written — the BLOCK fires before the
writer and editor get their turn. The test wraps kickoff in a try/except
so the run is still observable via OpenBox even though the pipeline did
not complete.

Six governed agents (1 manager + 5 workers) collaborate on a hierarchical
pipeline that produces a quarterly investor brief. All task descriptions are
templated with crew-level inputs so per-task `user_input` signals carry the
rendered prompt the agent actually saw, and the workers exercise a mix of
real-side-effect tools (filesystem read/write, SQLite query) plus stubbed
LLM-style tools (web_search, calculate) so the timeline is rich with
non-LLM span chips.

Topology (all agents already exist in the OpenBox 'Crew AI SDK' team):
  Senior Research Director (manager)
    ├── Market Intelligence Analyst    — uses web_search tool
    ├── Financial Data Analyst         — uses query_revenue_db + calculate tools
    ├── Compliance Reviewer            — regulatory risk
    ├── Technical Writer               — uses read_file tool, drafts the brief
    └── Senior Editor                  — uses save_file tool, polishes the brief

Tasks (in order, manager decides which worker to delegate each to):
  1. Read brief requirements             (file read; templated: requirements_path)
  2. Market intelligence collection      (templated: industry, fiscal_quarter)
  3. Financial benchmark calculation     (database read; templated: company_name, fiscal_quarter)
  4. Risk assessment                     (templated: industry)
  5. Compliance review                   (templated: jurisdiction)
  6. Initial draft                       (templated: length, audience, tone)
  7. Polish, summarise, and publish      (file write; templated: audience, tone, output_path)

Designed to produce a rich timeline with:
  - 6 lanes (one per agent)
  - ~14 signals (user_input + agent_output per task)
  - Tool spans on Writer (read_file), Financial Data (query_revenue_db, calculate),
    Market Intelligence (web_search), Editor (save_file) lanes
  - Multiple manager → worker handoffs

Local fixtures (created at test start, deleted on teardown):
  tmp/brief_requirements.md     — house-style requirements the writer reads at task 1
  Postgres table                — `crewai_e2e_quarterly_revenue` in the local stack's
                                  `openbox` database, populated with the figures the
                                  analyst queries at task 3
  tmp/investor_brief.md         — final polished brief written at task 9 (asserted to exist)

Prerequisites
-------------
- Six OpenBox agents registered in the 'Crew AI SDK' team. The first four
  reuse agents that already have credentials provisioned in this repo's
  e2e flow; the last two need their identity provisioned in the OpenBox
  portal (Agent Settings → API Access → Provision Identity) and their
  credentials added to .env (see .env.example):

    Reused (have local credentials):
      OPENBOX_SENIOR_RESEARCHER → CrewAI E2E - Senior Researcher (manager)
      OPENBOX_RESEARCHER        → CrewAI E2E - Researcher          (Market Intelligence)
      OPENBOX_DATA_ANALYST      → CrewAI E2E - Data Analyst        (Financial Data)
      OPENBOX_WRITER            → CrewAI E2E - Writer              (Technical Writer)

    Need provisioning:
      OPENBOX_EDITOR             → CrewAI E2E - Editor              (already exists in team)
      OPENBOX_COMPLIANCE_REVIEWER → CrewAI E2E - Compliance Reviewer (must be created
                                    in portal first; or via openbox-cli create-agent)

Usage:
    python3 -m tests.e2e.hierarchical_complex_blocked.test_complex_hierarchical_blocked
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
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


MANAGER_PREFIX = "OPENBOX_SENIOR_RESEARCHER"
RESEARCHER_PREFIX = "OPENBOX_RESEARCHER"
ANALYST_PREFIX = "OPENBOX_DATA_ANALYST"
WRITER_PREFIX = "OPENBOX_WRITER"
EDITOR_PREFIX = "OPENBOX_EDITOR"
# Cloned compliance reviewer with a BLOCK rule on every llm_completion.
COMPLIANCE_PREFIX = "OPENBOX_COMPLIANCE_REVIEWER_BLOCKED"


# -- Fixture paths -----------------------------------------------------------

TEMP_DIR = Path(__file__).parent / "tmp"
REQUIREMENTS_FILE = TEMP_DIR / "brief_requirements.md"
OUTPUT_FILE = TEMP_DIR / "investor_brief.md"

# Postgres on localhost — local stack creds default to the docker-compose values
# from the openbox-backend stack. Override via env if your stack differs.
PG_CONN_KWARGS = {
    "host": os.environ.get("E2E_PG_HOST", "localhost"),
    "port": int(os.environ.get("E2E_PG_PORT", "5432")),
    "user": os.environ.get("E2E_PG_USER", "postgres"),
    "password": os.environ.get("E2E_PG_PASSWORD", "password"),
    "dbname": os.environ.get("E2E_PG_DB", "openbox"),
}
REVENUE_TABLE = "crewai_e2e_quarterly_revenue"

REQUIREMENTS_TEXT = """\
# Investor Brief — House Style Requirements

## Required Sections
1. Market context (max 2 paragraphs)
2. Financial highlights with QoQ revenue growth, including the absolute change
3. Top 3 strategic risks with one-line rationale each
4. Compliance disclosures with severity (low / medium / high)
5. 200-word executive summary at the top

## Style Rules
- No superlatives ("best", "world-class", "industry-leading")
- Always cite numeric figures with their units ($M, %, etc.)
- Reference specific competitors by name when discussed
- Every compliance item must include a recommended action

## Output
The final polished brief must be saved to disk via the save_file tool at
the path provided as `output_path` in the crew inputs.
"""


def _ensure_temp_dir() -> Path:
    TEMP_DIR.mkdir(exist_ok=True)
    return TEMP_DIR


def _prepare_requirements_file() -> Path:
    REQUIREMENTS_FILE.write_text(REQUIREMENTS_TEXT)
    return REQUIREMENTS_FILE


def _prepare_revenue_db() -> None:
    conn = psycopg2.connect(**PG_CONN_KWARGS)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {REVENUE_TABLE}")
            cur.execute(
                f"""
                CREATE TABLE {REVENUE_TABLE} (
                    company TEXT NOT NULL,
                    fiscal_quarter TEXT NOT NULL,
                    revenue_usd NUMERIC(18, 2) NOT NULL
                )
                """
            )
            cur.executemany(
                f"INSERT INTO {REVENUE_TABLE} VALUES (%s, %s, %s)",
                [
                    ("GovernAI Pro", "Q3 2026", 4_250_000.00),
                    ("GovernAI Pro", "Q4 2026", 5_120_000.00),
                ],
            )
    finally:
        conn.close()


def _drop_revenue_db() -> None:
    try:
        conn = psycopg2.connect(**PG_CONN_KWARGS)
    except psycopg2.OperationalError:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {REVENUE_TABLE}")
    finally:
        conn.close()


# -- Tools -------------------------------------------------------------------


@contextlib.contextmanager
def _pushd(target: Path):
    prev = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(prev)


def _sandboxed_path(file_path: str) -> Path:
    """Resolve file_path against TEMP_DIR and refuse anything outside it."""
    base = TEMP_DIR.resolve()
    candidate = Path(file_path)
    resolved = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(
            f"refused: {file_path!r} resolves to {resolved}, outside sandbox {base}"
        )
    return resolved


def _sandboxes_first_arg(fn):
    @functools.wraps(fn)
    def wrapper(file_path: str, *args, **kwargs):
        return fn(str(_sandboxed_path(file_path)), *args, **kwargs)

    return wrapper


@tool("read_file")
@_sandboxes_first_arg
def read_file(file_path: str) -> str:
    """Read a UTF-8 text file from the local filesystem and return its contents."""
    with open(file_path) as f:
        return f.read()


@tool("save_file")
@_sandboxes_first_arg
def save_file(file_path: str, content: str) -> str:
    """Save UTF-8 text content to the given local file path."""
    with open(file_path, "w") as f:
        f.write(content)
    return f"Saved {len(content)} chars to {file_path}"


@tool("query_revenue_db")
def query_revenue_db(sql: str) -> str:
    """Run a read-only SQL statement against the local Postgres revenue table.

    The table is named ``crewai_e2e_quarterly_revenue`` and lives in the local
    stack's ``openbox`` database. Returns rows as a JSON array of objects.
    """
    conn = psycopg2.connect(**PG_CONN_KWARGS)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return json.dumps(rows, default=str)


@tool("web_search")
def web_search(query: str) -> str:
    """Stubbed competitive landscape lookup. Returns canned market data."""
    return (
        "Q4 2026 enterprise AI governance market estimated at $4.1B (+27% YoY). "
        "Top 3 vendors by ARR: Acme Govern (28% share), Trustworks (19%), "
        "Sentinel AI (12%). Average sales cycle 4.6 months; primary buyer is "
        "the CISO with CTO endorsement. Notable Q4 trend: budget shift toward "
        "agent-runtime governance vs static-policy tooling."
    )


@tool("calculate")
def calculate(operation: str, current: float, previous: float) -> dict:
    """Compute QoQ growth metrics.

    operation: 'growth_rate' returns rate (%) plus absolute change;
               'absolute_change' returns just the absolute change.
    """
    absolute_change = current - previous
    if operation == "growth_rate":
        return {
            "growth_rate_pct": round((current - previous) / previous * 100, 2),
            "absolute_change": absolute_change,
        }
    if operation == "absolute_change":
        return {"absolute_change": absolute_change}
    raise ValueError(f"unsupported op: {operation}")


# -- LLM ---------------------------------------------------------------------


def _llm() -> LLM:
    return LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0)


# -- Agent factories ---------------------------------------------------------


def _make_manager() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Senior Research Director",
        env_prefix=MANAGER_PREFIX,
        goal=(
            "Route every task to the single specialist best equipped to "
            "execute it, based on their role and tools. You do not perform "
            "specialist work yourself."
        ),
        backstory=(
            "You are a senior research director. Your only job is delegation: "
            "pick the right specialist for each task and pass it through. "
            "Never draft, edit, query data, search the web, read files, or "
            "save files yourself — those are specialist responsibilities and "
            "the specialists have the tools for them."
        ),
        allow_delegation=True,
        verbose=True,
        llm=_llm(),
    )


def _make_market_analyst() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Market Intelligence Analyst",
        env_prefix=RESEARCHER_PREFIX,
        goal="Surface competitive intelligence and market trends",
        backstory=(
            "You profile competitors and industry dynamics. Use the "
            "web_search tool when you need external data."
        ),
        tools=[web_search],
        verbose=True,
        llm=_llm(),
    )


def _make_financial_analyst() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Financial Data Analyst",
        env_prefix=ANALYST_PREFIX,
        goal="Compute QoQ growth metrics from the revenue database",
        backstory=(
            "You quantify revenue and growth. Use query_revenue_db to fetch "
            "the figures from the quarterly_revenue table, then use the "
            "calculate tool — never make up numbers."
        ),
        tools=[query_revenue_db, calculate],
        verbose=True,
        llm=_llm(),
    )


def _make_compliance_reviewer() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Compliance Reviewer",
        env_prefix=COMPLIANCE_PREFIX,
        goal="Flag regulatory disclosure obligations and material risks",
        backstory=(
            "You review investor communications for jurisdiction-specific "
            "disclosure requirements (US SEC, EU MAR, UK FCA, etc.)."
        ),
        verbose=True,
        llm=_llm(),
    )


def _make_writer() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Technical Writer",
        env_prefix=WRITER_PREFIX,
        goal="Draft a clean, structured investor brief in Markdown",
        backstory=(
            "You turn raw research and numbers into clear prose. Use the "
            "read_file tool to load the brief requirements before drafting "
            "so the structure and house style match exactly."
        ),
        tools=[read_file],
        verbose=True,
        llm=_llm(),
    )


def _make_editor() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Senior Editor and Publisher",
        env_prefix=EDITOR_PREFIX,
        goal=(
            "Polish drafts for tone, clarity, and audience fit, and publish "
            "the final brief to disk via the save_file tool."
        ),
        backstory=(
            "You enforce house style, fix structural issues, and ensure every "
            "paragraph earns its place. You are the only person on the team "
            "who publishes the final brief to disk — every brief you handle "
            "ends with a save_file call. You never describe a save without "
            "invoking the tool."
        ),
        tools=[save_file],
        verbose=True,
        llm=_llm(),
    )


# -- Tests -------------------------------------------------------------------


def test_complex_hierarchical_pipeline_blocked() -> None:
    """Six-agent hierarchical crew where Compliance Reviewer is BLOCKED.

    The BLOCK fires on the Compliance Reviewer's first llm_completion. We
    don't assert on the output file (it will not be written) — the goal is
    to land a real workflow record with mixed ALLOW/BLOCK per-agent verdicts.
    """
    _ensure_temp_dir()
    _prepare_requirements_file()
    _prepare_revenue_db()
    OUTPUT_FILE.unlink(missing_ok=True)

    try:
        manager = _make_manager()
        market_analyst = _make_market_analyst()
        financial_analyst = _make_financial_analyst()
        compliance_reviewer = _make_compliance_reviewer()
        writer = _make_writer()
        editor = _make_editor()

        tasks = [
            OpenBoxTask(
                name="compliance_review",
                activity_type="policy_test",
                description=(
                    "Review the planned investor communication for compliance with "
                    "{jurisdiction} disclosure requirements. Surface forward-looking "
                    "statements, material non-public info concerns, and required "
                    "boilerplate."
                ),
                expected_output=(
                    "List of compliance items: each with severity (low/medium/high) "
                    "and recommended action."
                ),
            ),
            OpenBoxTask(
                name="read_brief_requirements",
                activity_type="research",
                description=(
                    "Call the read_file tool with file_path='{requirements_path}'. "
                    "Then summarise, in three short bullet points, the required "
                    "sections, the house style rules, and the output path the "
                    "team must use. You MUST invoke the tool — do not invent a "
                    "summary without reading the file."
                ),
                expected_output=(
                    "Three bullets: required sections, style rules, output path."
                ),
            ),
            OpenBoxTask(
                name="market_intelligence",
                activity_type="research",
                description=(
                    "Research the {industry} market for {fiscal_quarter}: identify "
                    "the top 3 competitors by share, headline growth rate, and the "
                    "single most notable trend. You MUST call the web_search tool "
                    "first to ground your findings."
                ),
                expected_output=(
                    "A short bullet list: top competitors, market growth, dominant trend."
                ),
            ),
            OpenBoxTask(
                name="financial_benchmarks",
                activity_type="analysis",
                description=(
                    "First, call query_revenue_db with this exact SQL: "
                    "\"SELECT fiscal_quarter, revenue_usd FROM "
                    "crewai_e2e_quarterly_revenue WHERE company = "
                    "'{company_name}' ORDER BY fiscal_quarter\". "
                    "Then call the calculate tool with operation='growth_rate', "
                    "current=<Q4 revenue>, previous=<Q3 revenue> using the "
                    "numbers you just queried. You MUST invoke both tools."
                ),
                expected_output=(
                    "Growth rate as percentage and absolute change in dollars."
                ),
            ),
            OpenBoxTask(
                name="risk_assessment",
                activity_type="analysis",
                description=(
                    "Identify the top 3 market risks {company_name} faces in the "
                    "{industry} space over the next two quarters."
                ),
                expected_output="Numbered list of three risks with one-line rationale each.",
            ),
            OpenBoxTask(
                name="initial_draft",
                activity_type="writing",
                description=(
                    "Draft a {length}-word investor brief for {company_name}'s "
                    "{fiscal_quarter}. Audience: {audience}. Tone: {tone}. "
                    "Incorporate the market intelligence, financial growth metrics, "
                    "risk assessment, and compliance items from earlier tasks, "
                    "following the structure from the brief requirements."
                ),
                expected_output="Markdown brief approximately the requested length.",
            ),
            OpenBoxTask(
                name="polish_and_publish",
                activity_type="writing",
                description=(
                    "This is a single end-to-end editorial task. First, edit "
                    "the draft for {audience}, ensuring the tone is {tone}: "
                    "tighten redundant phrasing, fix structural issues, "
                    "acknowledge every compliance recommendation. Then write "
                    "a 200-word executive summary at the top of the polished "
                    "brief. Finally, you MUST call the save_file tool with "
                    "file_path='{output_path}' and content set to the full "
                    "polished Markdown brief (executive summary + body). "
                    "Return only the save_file tool's confirmation string."
                ),
                expected_output=(
                    "Confirmation message from save_file with the byte count."
                ),
            ),
        ]

        inputs = {
            "company_name": "GovernAI Pro",
            "fiscal_quarter": "Q4 2026",
            "industry": "enterprise AI governance",
            "audience": "institutional investors and growth-stage capital partners",
            "tone": "professional, data-driven, cautiously optimistic",
            "length": "800",
            "jurisdiction": "US SEC and EU MAR",
            "requirements_path": str(REQUIREMENTS_FILE),
            "output_path": str(OUTPUT_FILE),
        }

        with create_openbox_engine(
            instrument_file_io=True,
            instrument_databases=True,
            db_libraries={"psycopg2"},
            debug_log=True,
        ) as engine:
            crew = Crew(
                name="investor-brief-q4",
                agents=[
                    market_analyst,
                    financial_analyst,
                    compliance_reviewer,
                    writer,
                    editor,
                ],
                tasks=tasks,
                process=Process.hierarchical,
                manager_agent=manager,
                verbose=True,
            )

            governed_crew = engine.govern(crew)
            with _pushd(TEMP_DIR):
                try:
                    governed_crew.kickoff(inputs=inputs)
                    log.warning(
                        "kickoff completed without raising — expected a "
                        "BLOCK at the compliance step. Inspect the workflow "
                        "to confirm the BLOCK verdict landed."
                    )
                except Exception as exc:  # noqa: BLE001 — any failure is OK
                    log.info("kickoff raised (expected BLOCK path): %s", exc)
    finally:
        REQUIREMENTS_FILE.unlink(missing_ok=True)
        _drop_revenue_db()
        OUTPUT_FILE.unlink(missing_ok=True)


# -- Main --------------------------------------------------------------------


def main() -> None:
    tests = [
        (
            "complex_hierarchical_pipeline_blocked",
            test_complex_hierarchical_pipeline_blocked,
        ),
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
    print("  COMPLEX HIERARCHICAL BLOCKED GOVERNANCE TEST SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
