"""Base span data for governance payloads."""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from openbox.utils import format_span_id, format_trace_id

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan

STATUS_UNSET = "UNSET"


class Stage(enum.Enum):
    STARTED = "started"
    COMPLETED = "completed"


@dataclass
class SpanData:
    """Base span data sent in the ``spans[]`` array of hook payloads.

    Subclasses set ``hook_type`` and ``_match_attrs`` as class variables.
    Registration is automatic via ``__init_subclass__``.
    """

    hook_type: ClassVar[str] = ""
    _match_attrs: ClassVar[frozenset[str]] = frozenset()
    _registry: ClassVar[list[tuple[frozenset[str], type[SpanData]]]] = []

    stage: Stage
    span_id: str | None = None
    trace_id: str | None = None
    parent_span_id: str | None = None
    name: str | None = None
    kind: str | None = None
    start_time: int | None = None
    end_time: int | None = None
    duration_ns: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=lambda: {"code": STATUS_UNSET})
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    semantic_type: str | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls._match_attrs:
            SpanData._registry.append((cls._match_attrs, cls))

    @classmethod
    def classify(cls, attrs: dict[str, Any]) -> type[SpanData] | None:
        attr_keys = attrs.keys()
        for match_attrs, span_cls in cls._registry:
            if not match_attrs.isdisjoint(attr_keys):
                return span_cls
        return None

    @classmethod
    def from_otel_span(
        cls,
        span: ReadableSpan,
        attrs: dict[str, Any],
        body: dict[str, Any] | None,
    ) -> SpanData:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"hook_type": self.hook_type}
        for f in dataclasses.fields(self):
            v = getattr(self, f.name)
            if v is None:
                continue
            result[f.name] = v.value if isinstance(v, enum.Enum) else v
        return result

    @staticmethod
    def _common_from_otel(
        span: ReadableSpan,
        attrs: dict[str, Any],
    ) -> dict[str, Any]:
        ctx = span.get_span_context()
        if ctx is None:
            raise ValueError("Span has no context")
        parent = span.parent

        status_dict: dict[str, str] = {"code": STATUS_UNSET}
        if span.status is not None:
            status_dict = {"code": span.status.status_code.name}
            if span.status.description:
                status_dict["description"] = span.status.description

        events = []
        error = None
        for event in span.events or []:
            event_dict: dict[str, Any] = {"name": event.name}
            if event.timestamp:
                event_dict["timestamp"] = event.timestamp
            if event.attributes:
                event_dict["attributes"] = dict(event.attributes)
                if (
                    error is None
                    and event.name == "exception"
                    and event.attributes.get("exception.message")
                ):
                    error = str(event.attributes["exception.message"])
            events.append(event_dict)

        return {
            "stage": Stage.COMPLETED,
            "span_id": format_span_id(ctx.span_id),
            "trace_id": format_trace_id(ctx.trace_id),
            "parent_span_id": format_span_id(parent.span_id) if parent else None,
            "name": span.name,
            "kind": span.kind.name if span.kind else None,
            "start_time": span.start_time,
            "end_time": span.end_time,
            "duration_ns": (span.end_time - span.start_time)
            if span.start_time and span.end_time
            else None,
            "attributes": dict(attrs),
            "status": status_dict,
            "events": events,
            "error": error,
        }
