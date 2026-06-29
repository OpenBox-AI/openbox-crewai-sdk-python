"""httpx hooks and Client.send() body capture patching."""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace

from openbox.core.spans import HttpSpanData, Stage
from openbox.instrumentation.interceptors._runtime import (
    evaluate_started,
    get_current_trace_id,
    get_runtime,
    should_ignore_url,
)
from openbox.utils import format_span_id, format_trace_id, is_text_content_type

logger = logging.getLogger("openbox")


def httpx_request_hook(span: Any, request: Any) -> None:
    url = str(request.url) if request.url else ""
    if should_ignore_url(url):
        return

    trace_id = get_current_trace_id()
    if trace_id is None:
        return

    rt = get_runtime()
    binding = rt.binding_for_trace(trace_id) if rt else None

    method = (
        request.method.decode("utf-8", errors="replace")
        if isinstance(request.method, bytes)
        else str(request.method)
    )

    # Capture request body from stream if possible
    body_str: str | None = None
    if request.stream:
        try:
            if hasattr(request.stream, "body"):
                raw = request.stream.body
                if isinstance(raw, bytes):
                    body_str = raw.decode("utf-8", errors="replace")
                elif isinstance(raw, str):
                    body_str = raw
        except Exception:
            logger.debug("Failed to capture body data", exc_info=True)

    # Store body for on_end() completed evaluation
    span_ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    span_id = span_ctx.span_id if span_ctx else None
    if span_id and body_str and binding:
        binding.span_processor.store_body(span_id, "request_body", body_str)

    # Store request headers
    if span_id and request.headers and binding:
        binding.span_processor.store_body(span_id, "request_headers", dict(request.headers))

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


def httpx_response_hook(span: Any, request: Any, response: Any) -> None:
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

    # Capture response headers
    if response.headers:
        binding.span_processor.store_body(span_id, "response_headers", dict(response.headers))

    # Capture response body from stream if possible
    content_type = response.headers.get("Content-Type", "") if response.headers else ""
    if is_text_content_type(content_type) and response.stream:
        try:
            if hasattr(response.stream, "body"):
                raw = response.stream.body
                if isinstance(raw, bytes):
                    binding.span_processor.store_body(
                        span_id,
                        "response_body",
                        raw.decode("utf-8", errors="replace"),
                    )
        except Exception:
            logger.debug("Failed to capture body data", exc_info=True)


# Async versions of httpx hooks


async def httpx_async_request_hook(span: Any, request: Any) -> None:
    httpx_request_hook(span, request)


async def httpx_async_response_hook(span: Any, request: Any, response: Any) -> None:
    httpx_response_hook(span, request, response)


_original_httpx_send: Any = None
_original_httpx_async_send: Any = None


def setup_httpx_body_capture() -> None:
    """Patch Client.send/AsyncClient.send to capture bodies after materialization.

    The OTel instrumentor's hooks receive streams, not materialized bodies.
    """
    global _original_httpx_send, _original_httpx_async_send

    try:
        import httpx as httpx_lib
    except ImportError:
        return

    if _original_httpx_send is not None:
        return  # Already patched

    _original_httpx_send = httpx_lib.Client.send

    def _patched_send(self: Any, request: Any, **kwargs: Any) -> Any:
        response = _original_httpx_send(self, request, **kwargs)

        rt = get_runtime()
        if not rt:
            return response

        url = str(request.url) if request.url else ""
        if should_ignore_url(url):
            return response

        # Get current span to store body
        current_span = trace.get_current_span()
        if current_span is None:
            return response

        span_ctx = current_span.get_span_context()
        if span_ctx is None or not span_ctx.span_id:
            return response

        binding = rt.binding_for_trace(span_ctx.trace_id)
        if binding is None:
            return response

        span_id = span_ctx.span_id

        # Capture request body
        if request.content:
            content_type = request.headers.get("Content-Type", "")
            if is_text_content_type(content_type):
                if isinstance(request.content, bytes):
                    binding.span_processor.store_body(
                        span_id,
                        "request_body",
                        request.content.decode("utf-8", errors="replace"),
                    )

        # Capture response body
        response.read()  # Ensure body is materialized
        resp_ct = response.headers.get("Content-Type", "")
        if is_text_content_type(resp_ct) and response.content:
            binding.span_processor.store_body(
                span_id,
                "response_body",
                response.content.decode("utf-8", errors="replace"),
            )

        # Capture response headers
        binding.span_processor.store_body(span_id, "response_headers", dict(response.headers))

        return response

    httpx_lib.Client.send = _patched_send  # type: ignore[assignment]

    # Patch async client
    _original_httpx_async_send = httpx_lib.AsyncClient.send

    async def _patched_async_send(self: Any, request: Any, **kwargs: Any) -> Any:
        response = await _original_httpx_async_send(self, request, **kwargs)

        rt = get_runtime()
        if not rt:
            return response

        url = str(request.url) if request.url else ""
        if should_ignore_url(url):
            return response

        current_span = trace.get_current_span()
        if current_span is None:
            return response

        span_ctx = current_span.get_span_context()
        if span_ctx is None or not span_ctx.span_id:
            return response

        binding = rt.binding_for_trace(span_ctx.trace_id)
        if binding is None:
            return response

        span_id = span_ctx.span_id

        if request.content:
            content_type = request.headers.get("Content-Type", "")
            if is_text_content_type(content_type):
                if isinstance(request.content, bytes):
                    binding.span_processor.store_body(
                        span_id,
                        "request_body",
                        request.content.decode("utf-8", errors="replace"),
                    )

        await response.aread()
        resp_ct = response.headers.get("Content-Type", "")
        if is_text_content_type(resp_ct) and response.content:
            binding.span_processor.store_body(
                span_id,
                "response_body",
                response.content.decode("utf-8", errors="replace"),
            )

        binding.span_processor.store_body(span_id, "response_headers", dict(response.headers))

        return response

    httpx_lib.AsyncClient.send = _patched_async_send  # type: ignore[assignment]


def teardown_httpx_body_capture() -> None:
    global _original_httpx_send, _original_httpx_async_send

    try:
        import httpx as httpx_lib
    except ImportError:
        return

    if _original_httpx_send is not None:
        httpx_lib.Client.send = _original_httpx_send  # type: ignore[assignment]
        _original_httpx_send = None

    if _original_httpx_async_send is not None:
        httpx_lib.AsyncClient.send = _original_httpx_async_send  # type: ignore[assignment]
        _original_httpx_async_send = None
