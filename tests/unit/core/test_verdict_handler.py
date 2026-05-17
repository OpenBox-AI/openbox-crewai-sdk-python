from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from openbox.core.config import GovernanceConfig
from openbox.core.errors import GuardrailsValidationError
from openbox.core.types import GovernanceResponse, GuardrailsResult, Verdict
from openbox.core.verdict_handler import log_constraint, resolve_verdict


def _config(**overrides) -> GovernanceConfig:
    return GovernanceConfig(**overrides)


# log_constraint --------------------------------------------------------------


def test_log_constraint_emits_when_verdict_is_constrain(caplog):
    response = GovernanceResponse(
        verdict=Verdict.CONSTRAIN,
        reason="limited to read-only mode",
        policy_id="pol-42",
    )
    with caplog.at_level(logging.INFO, logger="openbox"):
        log_constraint(response, "task_start")
    assert any(
        "Governance constraint applied [task_start]: limited to read-only mode (policy: pol-42)"
        in r.message for r in caplog.records
    )


def test_log_constraint_silent_for_other_verdicts(caplog):
    for verdict in (Verdict.ALLOW, Verdict.REQUIRE_APPROVAL, Verdict.BLOCK, Verdict.HALT):
        response = GovernanceResponse(verdict=verdict, reason="x")
        with caplog.at_level(logging.INFO, logger="openbox"):
            log_constraint(response, "task_start")
    assert not any("constraint applied" in r.message for r in caplog.records)


def test_log_constraint_silent_when_no_reason(caplog):
    response = GovernanceResponse(verdict=Verdict.CONSTRAIN, reason=None)
    with caplog.at_level(logging.INFO, logger="openbox"):
        log_constraint(response, "task_start")
    assert not any("constraint applied" in r.message for r in caplog.records)


# resolve_verdict / _check_guardrails ----------------------------------------


def test_resolve_verdict_skips_hitl_when_disabled():
    response = GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL)
    wait = MagicMock()
    out = resolve_verdict(
        response, "task_start",
        config=_config(hitl_enabled=False),
        crew_name="crew-1",
        wait_for_approval=wait,
    )
    assert wait.call_count == 0
    assert out is response


def test_resolve_verdict_calls_wait_when_hitl_enabled():
    pending = GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL)
    approved = GovernanceResponse(verdict=Verdict.ALLOW)
    wait = MagicMock(return_value=approved)
    out = resolve_verdict(
        pending, "task_start",
        config=_config(hitl_enabled=True),
        crew_name="crew-1",
        wait_for_approval=wait,
    )
    assert wait.call_count == 1
    assert out is approved


def test_resolve_verdict_skips_wait_when_crew_excluded():
    response = GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL)
    wait = MagicMock()
    resolve_verdict(
        response, "task_start",
        config=_config(hitl_enabled=True, exclude_crews_hitl={"crew-1"}),
        crew_name="crew-1",
        wait_for_approval=wait,
    )
    assert wait.call_count == 0


def test_resolve_verdict_raises_guardrails_when_failed_without_redacted_input():
    response = GovernanceResponse(
        verdict=Verdict.ALLOW,
        guardrails_result=GuardrailsResult(
            validation_passed=False,
            input_type="activity_input",
            reasons=[{"reason": "PII detected"}],
        ),
    )
    with pytest.raises(GuardrailsValidationError, match="PII detected"):
        resolve_verdict(
            response, "task_start",
            config=_config(),
            crew_name="crew-1",
            wait_for_approval=MagicMock(),
        )


def test_resolve_verdict_silent_on_guardrails_with_redacted_input():
    response = GovernanceResponse(
        verdict=Verdict.ALLOW,
        guardrails_result=GuardrailsResult(
            validation_passed=False,
            input_type="activity_input",
            redacted_input={"description": "redacted"},
        ),
    )
    out = resolve_verdict(
        response, "task_start",
        config=_config(),
        crew_name="crew-1",
        wait_for_approval=MagicMock(),
    )
    assert out is response


def test_resolve_verdict_default_reason_when_validation_failed_with_no_reasons():
    response = GovernanceResponse(
        verdict=Verdict.ALLOW,
        guardrails_result=GuardrailsResult(
            validation_passed=False,
            input_type="activity_output",
        ),
    )
    with pytest.raises(GuardrailsValidationError, match="Guardrails output validation failed"):
        resolve_verdict(
            response, "task_end",
            config=_config(),
            crew_name="crew-1",
            wait_for_approval=MagicMock(),
        )


def test_resolve_verdict_logs_constraint(caplog):
    response = GovernanceResponse(
        verdict=Verdict.CONSTRAIN, reason="read-only", policy_id="pol-1",
    )
    with caplog.at_level(logging.INFO, logger="openbox"):
        out = resolve_verdict(
            response, "hook",
            config=_config(),
            crew_name="crew-1",
            wait_for_approval=MagicMock(),
        )
    assert out is response
    assert any("constraint applied [hook]: read-only (policy: pol-1)" in r.message
               for r in caplog.records)
