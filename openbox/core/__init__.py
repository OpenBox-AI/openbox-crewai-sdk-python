"""OpenBox Core — framework-agnostic governance client, types, and configuration."""

from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.errors import (
    GovernanceAPIError,
    GovernanceApprovalExpiredError,
    GovernanceBlockedError,
    GovernanceHaltError,
    OpenBoxAuthError,
    OpenBoxConfigError,
    OpenBoxError,
    OpenBoxInsecureURLError,
    OpenBoxNetworkError,
)
from openbox.core.payloads import (
    ActivityCompletedPayload,
    ActivityStartedPayload,
    HookPayload,
    WorkflowCompletedPayload,
    WorkflowStartedPayload,
)
from openbox.core.spans import SpanData
from openbox.core.types import (
    AgentContext,
    ApprovalResponse,
    GovernanceResponse,
    GuardrailsResult,
    Verdict,
)

__all__ = [
    "GovernanceClient",
    "GovernanceConfig",
    # Errors
    "OpenBoxError",
    "OpenBoxConfigError",
    "OpenBoxAuthError",
    "OpenBoxNetworkError",
    "OpenBoxInsecureURLError",
    "GovernanceAPIError",
    "GovernanceHaltError",
    "GovernanceBlockedError",
    "GovernanceApprovalExpiredError",
    # Payloads
    "WorkflowStartedPayload",
    "WorkflowCompletedPayload",
    "ActivityStartedPayload",
    "ActivityCompletedPayload",
    "HookPayload",
    # Types
    "Verdict",
    "GovernanceResponse",
    "ApprovalResponse",
    "GuardrailsResult",
    "AgentContext",
    "SpanData",
]
