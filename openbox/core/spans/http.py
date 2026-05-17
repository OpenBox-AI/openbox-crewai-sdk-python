"""HTTP request span data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from openbox.core.spans.base import SpanData
from openbox.utils import filter_body, redact_sensitive_headers

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan


@dataclass
class HttpSpanData(SpanData):
    """Span data for an HTTP request operation."""

    hook_type: ClassVar[str] = "http_request"
    _match_attrs: ClassVar[frozenset[str]] = frozenset({"http.method", "http.request.method"})

    http_method: str | None = None
    http_url: str | None = None
    request_body: str | None = None
    request_headers: dict[str, str] | None = None
    response_body: str | None = None
    response_headers: dict[str, str] | None = None
    http_status_code: int | None = None

    @classmethod
    def from_otel_span(
        cls,
        span: ReadableSpan,
        attrs: dict[str, Any],
        body: dict[str, Any] | None,
    ) -> HttpSpanData:
        """Build from an OTel ReadableSpan with captured body data."""
        url = attrs.get("http.url") or attrs.get("url.full") or attrs.get("http.target")
        return cls(
            **SpanData._common_from_otel(span, attrs),
            http_method=attrs.get("http.method") or attrs.get("http.request.method"),
            http_url=url,
            request_body=filter_body(body, "request_body", "request_headers"),
            request_headers=redact_sensitive_headers(
                body.get("request_headers") if body else None
            ),
            response_body=filter_body(body, "response_body", "response_headers"),
            response_headers=redact_sensitive_headers(
                body.get("response_headers") if body else None
            ),
            http_status_code=(
                attrs.get("http.status_code") or attrs.get("http.response.status_code")
            ),
        )
