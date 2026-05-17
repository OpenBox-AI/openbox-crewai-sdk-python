from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from openbox.core.errors import GuardrailsValidationError
from openbox.core.types import GovernanceResponse, Verdict

if TYPE_CHECKING:
    from openbox.core.config import GovernanceConfig

logger = logging.getLogger("openbox")


def log_constraint(response: GovernanceResponse, context: str) -> None:
    if response.verdict is not Verdict.CONSTRAIN or not response.reason:
        return
    suffix = f" (policy: {response.policy_id})" if response.policy_id else ""
    logger.info("Governance constraint applied [%s]: %s%s", context, response.reason, suffix)


def _check_guardrails(response: GovernanceResponse) -> None:
    gr = response.guardrails_result
    if gr is None or gr.validation_passed:
        return
    if gr.redacted_input is not None:
        return
    reasons = gr.get_reason_strings()
    if not reasons:
        label = "output " if gr.input_type == "activity_output" else ""
        reasons = [f"Guardrails {label}validation failed"]
    raise GuardrailsValidationError(reasons)


def _hitl_should_poll(response: GovernanceResponse, config: "GovernanceConfig", crew_name: str) -> bool:
    if not response.verdict.requires_approval():
        return False
    if not config.hitl_enabled:
        return False
    return crew_name not in (config.exclude_crews_hitl or set())


def resolve_verdict(
    response: GovernanceResponse,
    context: str,
    *,
    config: "GovernanceConfig",
    crew_name: str,
    wait_for_approval: Callable[[], GovernanceResponse],
) -> GovernanceResponse:
    if _hitl_should_poll(response, config, crew_name):
        response = wait_for_approval()
    _check_guardrails(response)
    log_constraint(response, context)
    return response


async def resolve_verdict_async(
    response: GovernanceResponse,
    context: str,
    *,
    config: "GovernanceConfig",
    crew_name: str,
    await_for_approval: Callable[[], Awaitable[GovernanceResponse]],
) -> GovernanceResponse:
    if _hitl_should_poll(response, config, crew_name):
        response = await await_for_approval()
    _check_guardrails(response)
    log_constraint(response, context)
    return response
