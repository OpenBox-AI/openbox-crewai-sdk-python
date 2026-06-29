"""urllib3 library hooks."""

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


def urllib3_request_hook(span: Any, pool: Any, request_info: Any) -> None:
    url = str(request_info.url) if request_info.url else ""

    # urllib3 url may be a relative path; reconstruct from pool
    if pool and not url.startswith("http"):
        scheme = "https" if getattr(pool, "scheme", "") == "https" else "http"
        host = getattr(pool, "host", "")
        port = getattr(pool, "port", "")
        port_str = f":{port}" if port else ""
        url = f"{scheme}://{host}{port_str}{url}"

    if should_ignore_url(url):
        return

    trace_id = get_current_trace_id()
    if trace_id is None:
        return

    rt = get_runtime()
    binding = rt.binding_for_trace(trace_id) if rt else None
    method = str(request_info.method) if request_info.method else "GET"

    body_str: str | None = None
    if request_info.body:
        headers = dict(request_info.headers) if request_info.headers else {}
        content_type = headers.get("Content-Type", "")
        if is_text_content_type(content_type):
            if isinstance(request_info.body, bytes):
                body_str = request_info.body.decode("utf-8", errors="replace")
            elif isinstance(request_info.body, str):
                body_str = request_info.body

    span_ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    span_id = span_ctx.span_id if span_ctx else None
    if span_id and body_str and binding:
        binding.span_processor.store_body(span_id, "request_body", body_str)

    headers_dict = dict(request_info.headers) if request_info.headers else None
    if span_id and headers_dict and binding:
        binding.span_processor.store_body(span_id, "request_headers", headers_dict)

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


def urllib3_response_hook(span: Any, pool: Any, response: Any) -> None:
    """Body capture only."""
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

    if hasattr(response, "headers") and response.headers:
        binding.span_processor.store_body(span_id, "response_headers", dict(response.headers))

    content_type = ""
    if hasattr(response, "headers") and response.headers:
        content_type = response.headers.get("Content-Type", "")
    if is_text_content_type(content_type):
        try:
            data = response.data
            if data and isinstance(data, bytes):
                binding.span_processor.store_body(
                    span_id,
                    "response_body",
                    data.decode("utf-8", errors="replace"),
                )
        except Exception:
            logger.debug("Failed to capture body data", exc_info=True)
