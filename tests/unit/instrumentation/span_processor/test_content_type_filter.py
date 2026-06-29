"""Test content-type defense-in-depth filtering."""

from __future__ import annotations

from openbox.utils import filter_body as _filter_body


class TestFilterBody:
    def test_allows_json_response(self) -> None:
        body = {
            "response_body": '{"data": "value"}',
            "response_headers": {"content-type": "application/json"},
        }
        assert _filter_body(body, "response_body", "response_headers") == '{"data": "value"}'

    def test_allows_text_html(self) -> None:
        body = {
            "response_body": "<html>hello</html>",
            "response_headers": {"content-type": "text/html; charset=utf-8"},
        }
        assert _filter_body(body, "response_body", "response_headers") == "<html>hello</html>"

    def test_blocks_binary_pdf(self) -> None:
        body = {
            "response_body": "binary-pdf-data",
            "response_headers": {"content-type": "application/pdf"},
        }
        assert _filter_body(body, "response_body", "response_headers") is None

    def test_blocks_image(self) -> None:
        body = {
            "response_body": "binary-image-data",
            "response_headers": {"content-type": "image/png"},
        }
        assert _filter_body(body, "response_body", "response_headers") is None

    def test_blocks_octet_stream(self) -> None:
        body = {
            "response_body": "binary-data",
            "response_headers": {"content-type": "application/octet-stream"},
        }
        assert _filter_body(body, "response_body", "response_headers") is None

    def test_no_headers_trusts_hook(self) -> None:
        body = {"response_body": "some data"}
        assert _filter_body(body, "response_body", "response_headers") == "some data"

    def test_no_body_returns_none(self) -> None:
        body = {"response_headers": {"content-type": "application/json"}}
        assert _filter_body(body, "response_body", "response_headers") is None

    def test_none_body_returns_none(self) -> None:
        assert _filter_body(None, "response_body", "response_headers") is None

    def test_works_for_request_body_too(self) -> None:
        body = {
            "request_body": '{"query": "SELECT *"}',
            "request_headers": {"content-type": "application/json"},
        }
        assert _filter_body(body, "request_body", "request_headers") == '{"query": "SELECT *"}'

    def test_blocks_request_binary(self) -> None:
        body = {
            "request_body": "binary-upload",
            "request_headers": {"content-type": "application/octet-stream"},
        }
        assert _filter_body(body, "request_body", "request_headers") is None
