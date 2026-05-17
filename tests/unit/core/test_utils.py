from __future__ import annotations

import pytest

from openbox.core.errors import OpenBoxInsecureURLError
from openbox.utils import (
    filter_body,
    format_span_id,
    format_trace_id,
    is_text_content_type,
    validate_url,
)


class TestIsTextContentType:
    @pytest.mark.parametrize("ct", ["text/plain", "text/html", "text/csv", "TEXT/HTML"])
    def test_text_prefix_matches(self, ct):
        assert is_text_content_type(ct) is True

    @pytest.mark.parametrize(
        "ct",
        [
            "application/json",
            "application/xml",
            "application/x-www-form-urlencoded",
        ],
    )
    def test_known_application_types_match(self, ct):
        assert is_text_content_type(ct) is True

    def test_charset_suffix_is_stripped_before_match(self):
        assert is_text_content_type("application/json; charset=utf-8") is True
        assert is_text_content_type("text/html; charset=utf-8") is True

    def test_uppercase_normalized(self):
        assert is_text_content_type("APPLICATION/JSON") is True

    def test_whitespace_around_subtype_is_stripped(self):
        assert is_text_content_type("application/json ; charset=utf-8") is True

    @pytest.mark.parametrize(
        "ct",
        [
            "application/octet-stream",
            "image/png",
            "image/jpeg",
            "audio/mpeg",
            "video/mp4",
            "application/pdf",
            "application/protobuf",
        ],
    )
    def test_binary_types_do_not_match(self, ct):
        assert is_text_content_type(ct) is False

    @pytest.mark.parametrize("ct", [None, ""])
    def test_empty_or_none_returns_false(self, ct):
        assert is_text_content_type(ct) is False


class TestFilterBody:
    def test_none_body_returns_none(self):
        assert filter_body(None, "request_body", "request_headers") is None

    def test_empty_body_dict_returns_none(self):
        assert filter_body({}, "request_body", "request_headers") is None

    def test_missing_content_returns_none(self):
        assert filter_body({"other": "x"}, "request_body", "request_headers") is None

    def test_empty_content_returns_none(self):
        body = {"request_body": "", "request_headers": {"Content-Type": "application/json"}}
        assert filter_body(body, "request_body", "request_headers") is None

    def test_no_headers_returns_content(self):
        body = {"request_body": "raw"}
        assert filter_body(body, "request_body", "request_headers") == "raw"

    def test_text_content_type_passes_through(self):
        body = {
            "request_body": '{"a": 1}',
            "request_headers": {"Content-Type": "application/json"},
        }
        assert filter_body(body, "request_body", "request_headers") == '{"a": 1}'

    def test_lowercase_content_type_header_works(self):
        body = {
            "request_body": "hi",
            "request_headers": {"content-type": "text/plain"},
        }
        assert filter_body(body, "request_body", "request_headers") == "hi"

    def test_binary_content_type_drops_body(self):
        body = {
            "request_body": "...binary...",
            "request_headers": {"Content-Type": "application/octet-stream"},
        }
        assert filter_body(body, "request_body", "request_headers") is None


class TestFormatTraceId:
    def test_zero_pads_to_32_hex(self):
        assert format_trace_id(0) == "0" * 32

    def test_known_value_lowercased(self):
        assert format_trace_id(0xABCDEF) == "00000000000000000000000000abcdef"

    def test_full_128_bit_value(self):
        max_v = (1 << 128) - 1
        assert format_trace_id(max_v) == "f" * 32


class TestFormatSpanId:
    def test_zero_pads_to_16_hex(self):
        assert format_span_id(0) == "0" * 16

    def test_known_value_lowercased(self):
        assert format_span_id(0xABCDEF) == "0000000000abcdef"

    def test_full_64_bit_value(self):
        max_v = (1 << 64) - 1
        assert format_span_id(max_v) == "f" * 16


class TestValidateUrl:
    def test_https_passes(self):
        assert validate_url("https://api.example.com") == "https://api.example.com"

    def test_strips_trailing_slash(self):
        assert validate_url("https://api.example.com/") == "https://api.example.com"

    def test_http_localhost_passes(self):
        assert validate_url("http://localhost:8086") == "http://localhost:8086"

    def test_http_127_0_0_1_passes(self):
        assert validate_url("http://127.0.0.1:8086") == "http://127.0.0.1:8086"

    def test_http_ipv6_localhost_passes(self):
        assert validate_url("http://[::1]:8086") == "http://[::1]:8086"

    def test_http_external_host_raises(self):
        with pytest.raises(OpenBoxInsecureURLError, match="HTTP is not allowed"):
            validate_url("http://api.example.com")

    def test_unsupported_scheme_raises(self):
        with pytest.raises(OpenBoxInsecureURLError, match="Unsupported URL scheme"):
            validate_url("ftp://api.example.com")
