"""Exception hierarchy for the OpenBox SDK."""

from __future__ import annotations


class OpenBoxError(Exception):
    """Base exception for all OpenBox SDK errors."""


class OpenBoxConfigError(OpenBoxError):
    """Invalid configuration at init time."""


class OpenBoxAuthError(OpenBoxError):
    """API key format or validation failure."""


class OpenBoxNetworkError(OpenBoxError):
    """Cannot reach the Core API during initialization."""


class OpenBoxInsecureURLError(OpenBoxError):
    """HTTP used for a non-localhost URL."""


class GovernanceAPIError(OpenBoxError):
    """Core API unreachable and fail_closed policy is active."""


class GovernanceHaltError(OpenBoxError):
    """BLOCK or HALT verdict at Layer 1 (pre-task) or Layer 2 (post-task)."""

    def __init__(
        self,
        message: str,
        *,
        verdict: str | None = None,
        reason: str | None = None,
        policy_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.reason = reason
        self.policy_id = policy_id


class GovernanceBlockedError(OpenBoxError):
    """BLOCK or HALT verdict at Layer 3 started stage."""

    def __init__(
        self,
        message: str,
        *,
        verdict: str | None = None,
        reason: str | None = None,
        policy_id: str | None = None,
        hook_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.reason = reason
        self.policy_id = policy_id
        self.hook_type = hook_type


class GovernanceApprovalExpiredError(OpenBoxError):
    """HITL approval window expired."""


class GuardrailsValidationError(OpenBoxError):
    """Guardrails validation failed and no redacted_input fallback was provided."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons) if reasons else "Guardrails validation failed")
        self.reasons = reasons


def raise_governance_block(
    info: dict | None,
    *,
    subject: str = "Operation",
    hook_type: str | None = None,
) -> None:
    """Raise the appropriate governance exception for a recorded block/halt verdict.

    `info` is the dict stored in `_llm_block_info_var`. A HALT verdict raises
    GovernanceHaltError; anything else raises GovernanceBlockedError.
    """
    info = info or {}
    verdict = info.get("verdict")
    reason = info.get("reason")
    policy_id = info.get("policy_id")
    span_name = info.get("span_name")

    action = "halted" if verdict == "halt" else "blocked"
    base = f"{subject} {action} by governance"
    if span_name:
        base = f"{base} ({span_name})"
    message = f"{base}: {reason}" if reason else base

    if verdict == "halt":
        raise GovernanceHaltError(
            message,
            verdict=verdict,
            reason=reason,
            policy_id=policy_id,
        )
    raise GovernanceBlockedError(
        message,
        verdict=verdict or "block",
        reason=reason,
        policy_id=policy_id,
        hook_type=hook_type,
    )
