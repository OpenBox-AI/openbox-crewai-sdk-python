"""SDK-agnostic governance interface used by hook modules.

Exposes ``is_configured``, ``evaluate_sync``, ``evaluate_async``, and
``extract_span_context`` backed by CrewAI's ``evaluate_started`` and
``HookRuntime``.
"""

from __future__ import annotations

from typing import Any

from openbox.instrumentation.interceptors._runtime import (
    evaluate_started,
    get_current_trace_id,
    get_runtime,
)


class _DictSpanData:
    """Attribute-access wrapper around a span dict for evaluate_started."""

    __slots__ = ("_d",)

    def __init__(self, d: dict[str, Any]) -> None:
        self._d = d

    @property
    def hook_type(self) -> str:
        return str(self._d.get("hook_type") or "")

    @property
    def span_id(self) -> str | None:
        return self._d.get("span_id")

    @property
    def name(self) -> str | None:
        return self._d.get("name")

    def to_dict(self) -> dict[str, Any]:
        return self._d


def is_configured() -> bool:
    return get_runtime() is not None


def extract_span_context(span: Any) -> tuple[str, str, str | None]:
    """Return (span_id_hex, trace_id_hex, parent_span_id_hex-or-None)."""
    span_ctx = (
        span.get_span_context()
        if hasattr(span, "get_span_context")
        else getattr(span, "context", None)
    )
    try:
        span_id = (
            format(span_ctx.span_id, "016x")
            if span_ctx and isinstance(span_ctx.span_id, int)
            else "0" * 16
        )
    except (AttributeError, TypeError):
        span_id = "0" * 16
    try:
        trace_id = (
            format(span_ctx.trace_id, "032x")
            if span_ctx and isinstance(span_ctx.trace_id, int)
            else "0" * 32
        )
    except (AttributeError, TypeError):
        trace_id = "0" * 32

    parent_span_id = None
    parent = getattr(span, "parent", None)
    if parent and isinstance(getattr(parent, "span_id", None), int):
        parent_span_id = format(parent.span_id, "016x")

    return span_id, trace_id, parent_span_id


def evaluate_sync(
    span: Any, identifier: str, span_data: dict[str, Any] | None = None
) -> None:
    if span_data is None:
        return
    trace_id = get_current_trace_id()
    if trace_id is None:
        return
    evaluate_started(trace_id, _DictSpanData(span_data))


async def evaluate_async(
    span: Any, identifier: str, span_data: dict[str, Any] | None = None
) -> None:
    evaluate_sync(span, identifier, span_data)
