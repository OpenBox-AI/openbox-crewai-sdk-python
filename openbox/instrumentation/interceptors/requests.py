"""requests library hooks."""

from __future__ import annotations

import logging
from typing import Any

from openbox.core.spans import HttpSpanData, Stage
from openbox.instrumentation.interceptors._runtime import (
    evaluate_started,
    get_current_trace_id,
    get_runtime,
    should_ignore_url,
)
from openbox.utils import format_span_id, format_trace_id, is_text_content_type

logger = logging.getLogger("openbox")


def requests_request_hook(span: Any, request: Any) -> None:
    url = str(request.url) if request.url else ""
    if should_ignore_url(url):
        return

    trace_id = get_current_trace_id()
    if trace_id is None:
        return

    rt = get_runtime()
    binding = rt.binding_for_trace(trace_id) if rt else None

    body_str: str | None = None
    if request.body:
        content_type = (request.headers or {}).get("Content-Type", "")
        if is_text_content_type(content_type):
            if isinstance(request.body, bytes):
                body_str = request.body.decode("utf-8", errors="replace")
            elif isinstance(request.body, str):
                body_str = request.body

    span_ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    span_id = span_ctx.span_id if span_ctx else None
    if span_id and body_str and binding:
        binding.span_processor.store_body(span_id, "request_body", body_str)

    if span_id and request.headers and binding:
        binding.span_processor.store_body(span_id, "request_headers", dict(request.headers))

    method = request.method or "GET"
    headers_dict = dict(request.headers) if request.headers else None
    span_data = HttpSpanData(
        stage=Stage.STARTED,
        span_id=format_span_id(span_id) if span_id else None,
        trace_id=format_trace_id(trace_id),
        name=f"HTTP {method}",
        attributes={"http.method": method, "http.url": url},
        http_method=method,
        http_url=url,
        request_body=body_str,
        request_headers=headers_dict,
    )

    evaluate_started(trace_id, span_data)


def requests_response_hook(span: Any, request: Any, response: Any) -> None:
    """Body capture only — no governance eval."""
    rt = get_runtime()
    if not rt:
        return

    trace_id = get_current_trace_id()
    if trace_id is None:
        return

    binding = rt.binding_for_trace(trace_id)
    if binding is None:
        return

    span_ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    span_id = span_ctx.span_id if span_ctx else None
    if not span_id:
        return

    url = str(request.url) if request.url else ""
    if should_ignore_url(url):
        return

    content_type = response.headers.get("Content-Type", "") if response.headers else ""
    if is_text_content_type(content_type):
        try:
            body = response.text
            if body:
                binding.span_processor.store_body(span_id, "response_body", body)
        except Exception:
            logger.debug("Failed to capture body data", exc_info=True)

    if response.headers:
        binding.span_processor.store_body(span_id, "response_headers", dict(response.headers))
