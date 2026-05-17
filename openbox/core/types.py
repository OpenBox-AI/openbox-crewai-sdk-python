"""Verdict enum, governance response types, and agent context."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any

from openbox.core.aip_signing import AgentIdentity

logger = logging.getLogger("openbox")

_LEGACY_VERDICT_MAP: dict[str, str] = {
    "continue": "allow",
    "stop": "halt",
    "require-approval": "require_approval",
    "request_approval": "require_approval",
}

_VERDICT_PRIORITY: dict[str, int] = {
    "allow": 1,
    "constrain": 2,
    "require_approval": 3,
    "block": 4,
    "halt": 5,
}


class Verdict(enum.Enum):
    """ALLOW (1) < CONSTRAIN (2) < REQUIRE_APPROVAL (3) < BLOCK (4) < HALT (5)"""

    ALLOW = "allow"
    CONSTRAIN = "constrain"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"
    HALT = "halt"

    @property
    def priority(self) -> int:
        return _VERDICT_PRIORITY[self.value]

    def should_stop(self) -> bool:
        return self in (Verdict.BLOCK, Verdict.HALT)

    def requires_approval(self) -> bool:
        return self is Verdict.REQUIRE_APPROVAL

    @classmethod
    def from_string(cls, value: str) -> Verdict:
        """Parse with backward compat for legacy values. Falls back to ALLOW."""
        normalized = value.strip().lower().replace("-", "_")
        normalized = _LEGACY_VERDICT_MAP.get(normalized, normalized)
        try:
            return cls(normalized)
        except ValueError:
            logger.warning("Unknown verdict value %r, falling back to ALLOW", value)
            return cls.ALLOW

    @classmethod
    def highest_priority(cls, *verdicts: Verdict) -> Verdict:
        if not verdicts:
            return cls.ALLOW
        return max(verdicts, key=lambda v: v.priority)


@dataclass
class GuardrailsResult:
    redacted_input: Any = None
    input_type: str | None = None
    validation_passed: bool = True
    reasons: list[dict[str, str]] = field(default_factory=list)

    def get_reason_strings(self) -> list[str]:
        return [r.get("reason", "") for r in self.reasons if r.get("reason")]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GuardrailsResult:
        return cls(
            redacted_input=data.get("redacted_input"),
            input_type=data.get("input_type"),
            validation_passed=data.get("validation_passed", True),
            reasons=data.get("reasons", []),
        )


@dataclass
class GovernanceResponse:
    verdict: Verdict
    reason: str | None = None
    policy_id: str | None = None
    risk_score: float | None = None
    governance_event_id: str | None = None
    guardrails_result: GuardrailsResult | None = None
    approval_id: str | None = None
    fallback_used: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GovernanceResponse:
        # Handles both v1.0 "action" field and v1.1+ "verdict" field
        verdict_str = data.get("verdict") or data.get("action", "allow")
        verdict = Verdict.from_string(str(verdict_str))

        guardrails_data = data.get("guardrails_result")
        guardrails_result = GuardrailsResult.from_dict(guardrails_data) if guardrails_data else None

        return cls(
            verdict=verdict,
            reason=data.get("reason"),
            policy_id=data.get("policy_id"),
            risk_score=data.get("risk_score"),
            governance_event_id=data.get("governance_event_id"),
            guardrails_result=guardrails_result,
            approval_id=data.get("approval_id"),
            fallback_used=data.get("fallback_used", False),
        )


@dataclass
class ApprovalResponse:
    verdict: Verdict
    reason: str | None = None
    approval_id: str | None = None
    approval_expiration_time: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalResponse:
        # Core uses "action" not "verdict"; "require_approval" = still pending
        verdict_str = data.get("action") or data.get("verdict", "allow")
        return cls(
            verdict=Verdict.from_string(str(verdict_str)),
            reason=data.get("reason"),
            approval_id=data.get("id"),
            approval_expiration_time=data.get("approval_expiration_time"),
        )


@dataclass
class AgentContext:
    role: str
    session_id: str
    run_id: str
    api_key: str
    crew_name: str
    crew_execution_id: str
    multi_agent_session_id: str | None = None
    identity: AgentIdentity | None = None
