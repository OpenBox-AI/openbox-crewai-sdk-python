"""Shared utilities."""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from openbox.core.errors import OpenBoxAuthError, OpenBoxInsecureURLError

if TYPE_CHECKING:
    from openbox.core.types import AgentContext

logger = logging.getLogger("openbox")

_API_KEY_PATTERN = re.compile(r"^obx_(live|test)_[a-zA-Z0-9_]+$")

_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_TEXT_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-www-form-urlencoded",
    }
)
_TEXT_CONTENT_PREFIXES = ("text/",)

SOURCE = "crewai-telemetry"

# Set to False by hooks/span_processor on BLOCK/HALT; checked by the LLM call hook.
_llm_allowed_var: ContextVar[bool] = ContextVar("_llm_allowed_var", default=True)

_llm_block_info_var: ContextVar[dict[str, Any] | None] = ContextVar(
    "_llm_block_info_var", default=None
)

# Per-Flow.kickoff() correlation id — every governance event during a flow run
# carries this so telemetry can stitch crews/agents back to a single Flow run.
_flow_execution_id_var: ContextVar[str | None] = ContextVar(
    "_flow_execution_id_var", default=None
)

# Set by the outermost multi-agent boundary (create_openbox_flow or GovernedCrew.kickoff)
# so every governance event in the run can be correlated by a shared id.
_multi_agent_session_id_var: ContextVar[str | None] = ContextVar(
    "_multi_agent_session_id_var", default=None
)

# Carries the delegating agent's DiD across CrewAI's delegate_work_to_coworker
# call so the receiving agent can emit a Handoff event identifying the origin.
_handoff_origin_did_var: ContextVar[str | None] = ContextVar(
    "_handoff_origin_did_var", default=None
)


@dataclass(slots=True)
class ExecutionFrame:
    """The agent and activity currently executing. Set around each agent task
    and read by governance evaluation to attribute activity to the acting agent;
    it nests and restores through delegation."""

    agent_context: AgentContext
    activity_context: dict[str, Any]


_current_execution_frame: ContextVar[ExecutionFrame | None] = ContextVar(
    "openbox_current_execution_frame", default=None
)


def set_current_execution_frame(
    agent_context: AgentContext,
    activity_context: dict[str, Any],
) -> Token[ExecutionFrame | None]:
    return _current_execution_frame.set(
        ExecutionFrame(agent_context=agent_context, activity_context=activity_context)
    )


def reset_current_execution_frame(token: Token[ExecutionFrame | None]) -> None:
    _current_execution_frame.reset(token)


def get_current_execution_frame() -> ExecutionFrame | None:
    return _current_execution_frame.get()


def rfc3339_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso8601_utc(value: str) -> datetime:
    # Python <3.11's fromisoformat rejects 'Z'; normalize to '+00:00'.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def truncate_body(payload: str | None, limit: int | None) -> str | None:
    if payload is None or limit is None or len(payload) <= limit:
        return payload
    dropped = len(payload) - limit
    return f"{payload[:limit]}...[truncated {dropped} bytes]"


REDACTED_VALUE = "***REDACTED***"

_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
    }
)


def redact_sensitive_headers(
    headers: dict[str, str] | None,
) -> dict[str, str] | None:
    if headers is None:
        return None
    return {
        name: (REDACTED_VALUE if name.lower() in _SENSITIVE_HEADERS else value)
        for name, value in headers.items()
    }


def validate_url(url: str) -> str:
    """Validate and normalize a Core API URL. HTTPS required for non-localhost."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()

    if scheme == "http" and hostname not in _LOCALHOST_HOSTS:
        raise OpenBoxInsecureURLError(
            f"HTTP is not allowed for non-localhost URLs. Use HTTPS for: {url}"
        )

    if scheme not in ("http", "https"):
        raise OpenBoxInsecureURLError(f"Unsupported URL scheme {scheme!r}. Use http:// or https://")

    return url.rstrip("/")


def validate_api_key_format(key: str, env_var_name: str | None = None) -> None:
    if not _API_KEY_PATTERN.match(key):
        source = f" (from {env_var_name})" if env_var_name else ""
        raise OpenBoxAuthError(
            f"Invalid API key format{source}. Expected pattern: obx_(live|test)_<alphanumeric>"
        )


def is_text_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    ct = content_type.lower().split(";")[0].strip()
    if ct in _TEXT_CONTENT_TYPES:
        return True
    return any(ct.startswith(prefix) for prefix in _TEXT_CONTENT_PREFIXES)


def filter_body(
    body: dict[str, Any] | None,
    body_key: str,
    headers_key: str,
) -> str | None:
    """Return body content only if its Content-Type is text-based.

    Defense-in-depth: hooks already filter, but this catches edge cases
    before the body reaches the governance payload.
    """
    if not body:
        return None
    content = body.get(body_key)
    if not content:
        return None
    headers = body.get(headers_key)
    if not headers:
        return content
    content_type = headers.get("content-type") or headers.get("Content-Type")
    if content_type and not is_text_content_type(content_type):
        logger.debug("Dropping %s with non-text content-type: %s", body_key, content_type)
        return None
    return content


def build_metadata(agent_ctx: AgentContext) -> dict[str, Any]:
    return {
        "crew_name": agent_ctx.crew_name,
        "crew_execution_id": agent_ctx.crew_execution_id,
    }


def format_trace_id(trace_id: int) -> str:
    return f"{trace_id:032x}"


def format_span_id(span_id: int) -> str:
    return f"{span_id:016x}"
