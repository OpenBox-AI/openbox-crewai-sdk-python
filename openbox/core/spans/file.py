"""File I/O span data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from openbox.core.spans.base import SpanData

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan


@dataclass
class FileSpanData(SpanData):
    """Span data for a file I/O operation."""

    hook_type: ClassVar[str] = "file_operation"
    _match_attrs: ClassVar[frozenset[str]] = frozenset({"file.path"})

    file_path: str | None = None
    file_mode: str | None = None
    file_operation: str | None = None
    data: Any = None
    bytes_read: int | None = None
    bytes_written: int | None = None
    lines_count: int | None = None

    @classmethod
    def from_otel_span(
        cls,
        span: ReadableSpan,
        attrs: dict[str, Any],
        body: dict[str, Any] | None,
    ) -> FileSpanData:
        """Build from an OTel ReadableSpan with captured body data."""
        return cls(
            **SpanData._common_from_otel(span, attrs),
            file_path=attrs.get("file.path"),
            file_mode=attrs.get("file.mode"),
            file_operation=attrs.get("file.operation"),
            semantic_type=attrs.get("semantic.type") or attrs.get("semantic_type"),
            data={
                "file_path": attrs.get("file.path"),
                "file_mode": attrs.get("file.mode"),
                "file_operation": attrs.get("file.operation"),
                "bytes_read": body.get("bytes_read") if body else None,
                "bytes_written": body.get("bytes_written") if body else None,
                "lines_count": body.get("lines_count") if body else None,
                "content": body.get("data") if body else None,
            },
            bytes_read=body.get("bytes_read") if body else None,
            bytes_written=body.get("bytes_written") if body else None,
            lines_count=body.get("lines_count") if body else None,
        )
