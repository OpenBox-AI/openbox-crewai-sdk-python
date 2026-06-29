"""Database query span data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from openbox.core.spans.base import SpanData

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan


@dataclass
class DbSpanData(SpanData):
    """Span data for a database query operation."""

    hook_type: ClassVar[str] = "db_query"
    _match_attrs: ClassVar[frozenset[str]] = frozenset({"db.system"})

    db_system: str | None = None
    db_name: str | None = None
    db_operation: str | None = None
    db_statement: str | None = None
    server_address: str | None = None
    server_port: int | None = None
    rowcount: int | None = None
    data: dict[str, Any] | None = None

    @classmethod
    def from_otel_span(
        cls,
        span: ReadableSpan,
        attrs: dict[str, Any],
        body: dict[str, Any] | None,
    ) -> DbSpanData:
        """Build from an OTel ReadableSpan with captured body data."""
        return cls(
            **SpanData._common_from_otel(span, attrs),
            db_system=attrs.get("db.system"),
            db_name=attrs.get("db.name"),
            db_operation=attrs.get("db.operation"),
            db_statement=attrs.get("db.statement"),
            server_address=attrs.get("server.address") or attrs.get("net.peer.name"),
            server_port=attrs.get("server.port") or attrs.get("net.peer.port"),
            rowcount=body.get("rowcount") if body else None,
            semantic_type=attrs.get("semantic.type") or attrs.get("semantic_type"),
            data={
                "db_system": attrs.get("db.system"),
                "db_name": attrs.get("db.name"),
                "db_operation": attrs.get("db.operation"),
                "db_statement": attrs.get("db.statement"),
                "server_address": attrs.get("server.address") or attrs.get("net.peer.name"),
                "server_port": attrs.get("server.port") or attrs.get("net.peer.port"),
                "rowcount": body.get("rowcount") if body else None,
            },
        )
