from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.booking_policy import (
    BookingPolicyError,
    BookingPolicyNotConfigured,
    parse_booking_times,
    validate_classroom_hours,
)


def test_rejects_naive_and_reversed_times():
    with pytest.raises(BookingPolicyError, match="time zone"):
        parse_booking_times("2026-10-01T10:00:00", "2026-10-01T11:00:00")
    with pytest.raises(BookingPolicyError, match="after"):
        parse_booking_times("2026-10-01T11:00:00+05:30", "2026-10-01T10:00:00+05:30")


def test_classroom_hours_block_partial_overlap_but_allow_boundary():
    settings = SimpleNamespace(
        classroom_restricted_days="1,2,3,4,5",
        classroom_restricted_start="09:00",
        classroom_restricted_end="17:00",
    )
    room = {"is_general_classroom": True}
    with patch("core.booking_policy.get_settings", return_value=settings):
        start, end = parse_booking_times("2026-10-01T08:30:00+05:30", "2026-10-01T09:30:00+05:30")
        with pytest.raises(BookingPolicyError, match="college hours"):
            validate_classroom_hours(room, start, end)
        start, end = parse_booking_times("2026-10-01T17:00:00+05:30", "2026-10-01T18:00:00+05:30")
        validate_classroom_hours(room, start, end)


def test_special_room_ignores_classroom_hours():
    start, end = parse_booking_times("2026-10-01T10:00:00+05:30", "2026-10-01T11:00:00+05:30")
    validate_classroom_hours({"is_general_classroom": False}, start, end)


def test_unconfigured_classroom_policy_allows_any_time():
    settings = SimpleNamespace(
        classroom_restricted_days="",
        classroom_restricted_start="",
        classroom_restricted_end="",
    )
    start, end = parse_booking_times("2026-10-01T10:00:00+05:30", "2026-10-01T11:00:00+05:30")
    with patch("core.booking_policy.get_settings", return_value=settings):
        validate_classroom_hours({"is_general_classroom": True}, start, end)


def test_partially_configured_classroom_policy_fails_closed():
    settings = SimpleNamespace(
        classroom_restricted_days="1,2,3,4,5",
        classroom_restricted_start="",
        classroom_restricted_end="",
    )
    start, end = parse_booking_times("2026-10-01T10:00:00+05:30", "2026-10-01T11:00:00+05:30")
    with patch("core.booking_policy.get_settings", return_value=settings):
        with pytest.raises(BookingPolicyNotConfigured):
            validate_classroom_hours({"is_general_classroom": True}, start, end)
