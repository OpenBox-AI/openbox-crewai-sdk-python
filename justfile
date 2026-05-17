# OpenBox CrewAI SDK

# List available recipes
default:
    @just --list

# Run unit tests
test:
    uv run python3 -m pytest tests/unit/ -v

# Run e2e tests (non-interactive only)
test-e2e:
    uv run python3 -m pytest tests/e2e/ -v -k "not require_approval"

# Run e2e tests including HITL approval tests (requires manual action in OpenBox UI)
test-e2e-hitl:
    OPENBOX_HITL_TESTS=1 uv run python3 -m pytest tests/e2e/ -v

# Run all non-interactive tests
test-all:
    uv run python3 -m pytest tests/unit/ tests/e2e/ -v -k "not require_approval"

# Lint with ruff
lint:
    uv run ruff check openbox/ tests/

# Format with ruff
fmt:
    uv run ruff format openbox/ tests/

# Type check with mypy
typecheck:
    uv run pyright openbox/
