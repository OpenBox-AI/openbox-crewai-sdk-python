# E2E Guardrails Tests

End-to-end tests for guardrail behavior against a live OpenBox environment.

These scenarios verify that the SDK handles guardrail verdicts correctly for:

- PII detection
- content filtering
- toxicity detection
- ban words

They cover both blocking and logging behavior.

## Purpose

These tests are intended for integration validation, not normal package consumers. They require live platform configuration and real credentials.

## Prerequisites

- Python `>=3.10,<3.14`
- SDK installed: `pip install -e .` from repo root
- Access to a live OpenBox Core API
- OpenAI API key

## Platform Setup

Create **2 agents** in the OpenBox platform. Each agent gets all 4 guardrail types configured as a pipeline.

### Agent 1: `e2e-guardrails-block`

All guardrails with **Block on Violation = ON** and **Log Violations = ON**.

### Agent 2: `e2e-guardrails-log`

All guardrails with **Block on Violation = OFF** and **Log Violations = ON**.

### Guardrail Configuration (both agents)

Each guardrail uses the processing stage that makes sense for its purpose:
- **PII** uses **pre-processing** — blocks PII in task descriptions before the LLM sees them
- **Content Filtering / Toxicity / Ban Words** use **post-processing** — catches violations in LLM output

The SDK sends `activity_input` as `[{"description": "..."}, {"expected_output": "..."}]` and `activity_output` as `{"result": "..."}` (matching DeepAgents format).

#### PII Detection
| Setting | Value |
|---|---|
| Processing State | **pre-processing** |
| Fields to Check | `activity_input.description` |
| PII Entities | EMAIL_ADDRESS, PHONE_NUMBER, PERSON |

#### Content Filtering
| Setting | Value |
|---|---|
| Processing State | post-processing |
| Fields to Check | `activity_output.result` |
| Detection Threshold | 0.60 |
| Validation Method | sentence |

#### Toxicity Detection
| Setting | Value |
|---|---|
| Processing State | post-processing |
| Fields to Check | `activity_output.result` |
| Toxicity Threshold | 0.50 |
| Validation Method | full_text |

#### Ban Words
| Setting | Value |
|---|---|
| Processing State | post-processing |
| Fields to Check | `activity_output.result` |
| Banned Words | acme, competitor, forbidden |
| Max Levenshtein Distance | 0 |

## Environment Setup

```bash
cp .env.example .env
# Edit .env with your actual keys
```

## Running

```bash
# From repo root

# Run all block tests (4 guardrail types)
python3 -m tests.e2e.guardrails.test_block

# Run all log tests (4 guardrail types)
python3 -m tests.e2e.guardrails.test_log
```

## How the Tests Work

### PII (pre-processing)
The PII test puts actual PII directly in the **task description** (e.g., email, phone, SSN). The guardrail evaluates `activity_input.description` at pre-processing and catches the PII before it reaches the LLM.

### Content Filtering / Toxicity / Ban Words (post-processing)
These tests use task descriptions that prompt the LLM to generate violating content. The guardrail evaluates `activity_output.result` at post-processing after the LLM responds.

## What to Verify

### Block Tests (`test_block.py`)
- Console shows `[PASS]` for each guardrail type
- `GovernanceHaltError` raised with verdict, reason, and policy_id
- Agent halted flag is True
- Task 3 (post-block) never executes
- OpenBox dashboard shows the blocked event with violation details

### Log Tests (`test_log.py`)
- Console shows `[PASS]` for each guardrail type
- Both tasks complete without exception
- OpenBox dashboard shows logged violations
- For PII: input may contain redaction tags (`<EMAIL_ADDRESS>`, `<PHONE_NUMBER>`)
- For ban words: banned words may be replaced with initials

## Troubleshooting

- **LLM refuses trigger prompt** (content filter/toxicity): lower the detection threshold in the platform, or adjust the prompt
- **PII guardrail does not trigger**: verify Fields to Check is `activity_input.description` and processing state is `pre-processing`
- **Output guardrail does not trigger**: verify Fields to Check is `activity_output.result` and processing state is `post-processing`
- **`OpenBoxConfigError: Environment variable X is not set`**: check `.env` file path and variable names
