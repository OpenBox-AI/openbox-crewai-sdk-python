"""Tests for tolerant ISO 8601 parsing."""

from __future__ import annotations

from datetime import timezone

import pytest

from openbox.utils import parse_iso8601_utc


@pytest.mark.parametrize(
    "value",
    [
        "2026-05-14T10:00:00Z",
        "2026-05-14T10:00:00+00:00",
        "2026-05-14T10:00:00.123456Z",
        "2026-05-14T10:00:00.123456+00:00",
    ],
)
def test_parse_iso8601_utc_accepts_both_z_and_offset(value: str) -> None:
    parsed = parse_iso8601_utc(value)
    assert parsed.year == 2026 and parsed.month == 5 and parsed.day == 14
    assert parsed.hour == 10 and parsed.minute == 0 and parsed.second == 0
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(None)


def test_parse_iso8601_utc_invalid_raises() -> None:
    with pytest.raises(ValueError):
        parse_iso8601_utc("not a date")


def test_z_and_offset_produce_equal_datetimes() -> None:
    assert parse_iso8601_utc("2026-05-14T10:00:00Z") == parse_iso8601_utc(
        "2026-05-14T10:00:00+00:00"
    )
