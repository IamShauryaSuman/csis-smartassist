"""
Google Calendar integration for the Space Reservation Engine.

Uses a Workspace Service Account to query FreeBusy data across all
department room calendars in a single round-trip. Also supports
inserting calendar events for approved bookings.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from core.config import get_settings
from core.rooms import get_all_rooms, get_calendar_ids, get_room_by_id

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_calendar_service() -> Any:
    """Build an authorized Google Calendar API service object."""
    settings = get_settings()
    info = settings.google_service_account_info
    if not info:
        raise RuntimeError("Google service account credentials not configured.")

    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


async def query_freebusy(
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    room_ids: list[str] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Query FreeBusy data for all (or specified) department rooms.

    Args:
        time_min: Start of the query window (defaults to now).
        time_max: End of the query window (defaults to 7 days from now).
        room_ids: Optional list of room IDs to filter. If None, queries all rooms.

    Returns:
        Dict mapping room_id to a list of busy periods, each with 'start' and 'end' ISO strings.
    """
    now = datetime.now(timezone.utc)
    time_min = time_min or now
    time_max = time_max or now + timedelta(days=7)

    rooms = await get_all_rooms()
    
    # Build the calendar ID list
    if room_ids:
        calendar_items = []
        for room in rooms:
            if room["id"] in room_ids:
                calendar_items.append({"id": room["calendar_id"]})
    else:
        calendar_items = [{"id": cid} for cid in await get_calendar_ids()]

    if not calendar_items:
        return {}

    try:
        service = _get_calendar_service()
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "timeZone": "Asia/Kolkata",
            "items": calendar_items,
        }

        result = service.freebusy().query(body=body).execute()
        calendars = result.get("calendars", {})

        # Map calendar IDs back to room IDs
        cal_to_room: dict[str, str] = {}
        for room in rooms:
            cal_to_room[room["calendar_id"]] = room["id"]

        availability: dict[str, list[dict[str, str]]] = {}
        for cal_id, data in calendars.items():
            room_id = cal_to_room.get(cal_id, cal_id)
            availability[room_id] = [
                {"start": period["start"], "end": period["end"]}
                for period in data.get("busy", [])
            ]

        return availability

    except Exception:
        logger.exception("Failed to query Google Calendar FreeBusy API")
        return {}


async def create_calendar_event(
    room_id: str,
    title: str,
    start_time: datetime,
    end_time: datetime,
    description: str = "",
    attendee_email: str = "",
) -> dict[str, Any] | None:
    """Create a calendar event on the specified room's calendar or fallback calendar.

    Called when an admin approves a booking request.

    Args:
        room_id: The room ID from the manifest.
        title: Event title.
        start_time: Event start.
        end_time: Event end.
        description: Event description.
        attendee_email: Optional attendee email to invite.

    Returns:
        The created event resource, or None on failure.
    """
    settings = get_settings()
    room = await get_room_by_id(room_id)
    room_name = room["name"] if room else room_id

    try:
        service = _get_calendar_service()
    except Exception as e:
        logger.error("Cannot create calendar event — Google Calendar service unavailable: %s", e)
        return None

    # Ensure timezone awareness (default to IST: UTC+5:30)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=ist_tz)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=ist_tz)

    event_body: dict[str, Any] = {
        "summary": title,
        "description": description,
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": "Asia/Kolkata",
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": "Asia/Kolkata",
        },
    }

    raw_override = getattr(settings, "notification_override_email", "")
    override_email = raw_override if isinstance(raw_override, str) else ""
    effective_attendee = override_email if override_email else attendee_email
    if effective_attendee:
        event_body["attendees"] = [{"email": effective_attendee}]

    # Determine candidate calendars in priority order:
    # 1. Configured global calendar override (GOOGLE_CALENDAR_ID)
    # 2. Room-specific calendar_id
    # 3. Service account's "primary" calendar
    candidates: list[str] = []
    if settings.google_calendar_id:
        candidates.append(settings.google_calendar_id)
    if room and room.get("calendar_id"):
        candidates.append(room["calendar_id"])
    if "primary" not in candidates:
        candidates.append("primary")

    last_error = None
    for cal_id in candidates:
        try:
            logger.info("Attempting to insert calendar event into '%s'...", cal_id)
            event = (
                service.events()
                .insert(
                    calendarId=cal_id,
                    body=event_body,
                    sendUpdates="all" if effective_attendee else "none",
                )
                .execute()
            )
            logger.info(
                "Successfully created calendar event '%s' on calendar '%s' for room '%s'",
                event.get("id"),
                cal_id,
                room_name,
            )
            return event
        except Exception as e:
            if "attendee" in str(e).lower() and "attendees" in event_body:
                logger.warning("Calendar '%s' rejected attendee: %s. Retrying without attendees...", cal_id, e)
                body_no_attendees = {k: v for k, v in event_body.items() if k != "attendees"}
                try:
                    event = (
                        service.events()
                        .insert(
                            calendarId=cal_id,
                            body=body_no_attendees,
                            sendUpdates="none",
                        )
                        .execute()
                    )
                    logger.info(
                        "Successfully created calendar event '%s' on calendar '%s' for room '%s' (without attendees)",
                        event.get("id"),
                        cal_id,
                        room_name,
                    )
                    return event
                except Exception as retry_err:
                    e = retry_err
            last_error = e
            logger.warning("Failed to insert into calendar '%s': %s", cal_id, e)
            continue

    logger.error(
        "All calendar candidates failed for booking in room '%s'. Last error: %s",
        room_name,
        last_error,
    )
    return None


async def format_availability_for_llm(
    availability: dict[str, list[dict[str, str]]],
) -> str:
    """Format FreeBusy data as human-readable text for the LLM context window."""
    lines: list[str] = []
    rooms = await get_all_rooms()
    for room in rooms:
        room_id = room["id"]
        busy_slots = availability.get(room_id, [])
        lines.append(f"**{room['name']}** (ID: {room_id}):")
        if not busy_slots:
            lines.append("  - ✅ Fully available in the queried time window")
        else:
            for slot in busy_slots:
                lines.append(f"  - ❌ Busy: {slot['start']} → {slot['end']}")
        lines.append("")
    return "\n".join(lines)
