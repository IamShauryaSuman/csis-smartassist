"""Validated booking times and configurable general-classroom restrictions."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.config import get_settings

IST = ZoneInfo("Asia/Kolkata")


class BookingPolicyError(ValueError):
    pass


class BookingPolicyNotConfigured(BookingPolicyError):
    pass


def parse_booking_times(start_value: str, end_value: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.fromisoformat(start_value)
        end = datetime.fromisoformat(end_value)
    except ValueError as exc:
        raise BookingPolicyError("Start and end must be ISO 8601 timestamps.") from exc
    if start.tzinfo is None or end.tzinfo is None:
        raise BookingPolicyError("Start and end must include a time zone offset.")
    if end <= start:
        raise BookingPolicyError("End time must be after start time.")
    return start, end


def validate_classroom_hours(room: dict, start: datetime, end: datetime) -> None:
    """Reject any overlap with configured college hours for general classrooms."""
    if not room.get("is_general_classroom"):
        return

    settings = get_settings()
    values = (
        settings.classroom_restricted_days,
        settings.classroom_restricted_start,
        settings.classroom_restricted_end,
    )
    if not all(values):
        raise BookingPolicyNotConfigured("General classroom booking hours are not configured.")
    try:
        weekdays = {int(day.strip()) for day in values[0].split(",")}
        blocked_start = time.fromisoformat(values[1])
        blocked_end = time.fromisoformat(values[2])
        if not weekdays or any(day < 1 or day > 7 for day in weekdays) or blocked_end <= blocked_start:
            raise ValueError
    except ValueError as exc:
        raise BookingPolicyNotConfigured("General classroom booking hours are invalid.") from exc

    local_start = start.astimezone(IST)
    local_end = end.astimezone(IST)
    day = local_start.date()
    while day <= local_end.date():
        if day.isoweekday() in weekdays:
            restricted_start = datetime.combine(day, blocked_start, IST)
            restricted_end = datetime.combine(day, blocked_end, IST)
            if local_start < restricted_end and local_end > restricted_start:
                raise BookingPolicyError("General classrooms cannot be booked during college hours.")
        day += timedelta(days=1)
