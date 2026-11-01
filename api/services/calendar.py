"""
Google Calendar integration for the Space Reservation Engine.

Uses account OAuth or a Workspace service account to inspect one shared
calendar or per-room calendars, and create events for approved bookings.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from core.config import get_settings
from core.rooms import calendar_id_for_room, get_all_rooms, get_room_by_id

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_calendar_service() -> Any:
    """Build an authorized Google Calendar API service object."""
    settings = get_settings()
    if settings.google_calendar_refresh_token:
        if not settings.google_calendar_client_id or not settings.google_calendar_client_secret:
            raise RuntimeError("Calendar OAuth client credentials are not configured.")
        credentials = UserCredentials(
            token=None,
            refresh_token=settings.google_calendar_refresh_token,
            client_id=settings.google_calendar_client_id,
            client_secret=settings.google_calendar_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
    else:
        info = settings.google_service_account_info
        if not info:
            raise RuntimeError("Google Calendar credentials not configured.")
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
        if settings.google_calendar_subject:
            credentials = credentials.with_subject(settings.google_calendar_subject)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _event_room_id(event: dict[str, Any], rooms: list[dict]) -> str | None:
    """Identify the room represented by an event on a shared calendar."""
    private = event.get("extendedProperties", {}).get("private", {})
    tagged_id = private.get("room_id")
    if tagged_id:
        return tagged_id if any(room["id"] == tagged_id for room in rooms) else None

    # Existing room schedules may identify the room in Location or in the
    # title; require one unambiguous room match.
    text = " ".join((event.get("location") or "", event.get("summary") or ""))
    matches = [room["id"] for room in rooms if any(
        re.search(rf"(?<![\w-]){re.escape(label)}(?![\w-])", text, flags=re.IGNORECASE)
        for label in (room["name"], room["id"])
    )]
    return matches[0] if len(matches) == 1 else None


def _event_boundary(value: dict[str, str]) -> str | None:
    """Convert a Calendar event boundary to an ISO timestamp."""
    if value.get("dateTime"):
        return value["dateTime"]
    if value.get("date"):
        return datetime.fromisoformat(value["date"]).replace(tzinfo=IST).isoformat()
    return None


def _event_id_for_booking(booking_id: str) -> str | None:
    try:
        return "b" + UUID(booking_id).hex
    except ValueError:
        return None


async def get_calendar_event_for_booking(room_id: str, booking_id: str) -> dict[str, Any] | None:
    """Find an earlier event for a booking whose DB approval did not finish."""
    event_id = _event_id_for_booking(booking_id)
    room = await get_room_by_id(room_id)
    cal_id = calendar_id_for_room(room) if room else None
    if not event_id or not cal_id:
        return None
    try:
        event = _get_calendar_service().events().get(calendarId=cal_id, eventId=event_id).execute()
    except HttpError as exc:
        if exc.resp.status == 404:
            return None
        raise
    private = event.get("extendedProperties", {}).get("private", {})
    if private.get("booking_id") == booking_id and private.get("room_id") == room_id:
        return event
    return None


def _query_shared_calendar(
    service: Any, calendar_id: str, rooms: list[dict],
    time_min: datetime, time_max: datetime,
) -> dict[str, list[dict[str, str]] | None]:
    """List expanded events so a shared calendar can be split by room."""
    availability: dict[str, list[dict[str, str]] | None] = {
        room["id"]: [] for room in rooms
    }
    page_token = None
    while True:
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            maxResults=2500,
            pageToken=page_token,
        ).execute()
        for event in result.get("items", []):
            if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                continue
            room_id = _event_room_id(event, rooms)
            start = _event_boundary(event.get("start", {}))
            end = _event_boundary(event.get("end", {}))
            if room_id is None or not start or not end:
                # An unclassified event might be a room booking. Do not report
                # any room on this shared calendar as free in this window.
                return {room["id"]: None for room in rooms}
            availability[room_id].append({"start": start, "end": end})  # type: ignore[union-attr]
        page_token = result.get("nextPageToken")
        if not page_token:
            return availability


async def query_freebusy(
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    room_ids: list[str] | None = None,
) -> dict[str, list[dict[str, str]] | None]:
    """Return busy periods per room; None means availability is unknown.

    Args:
        time_min: Start of the query window (defaults to now).
        time_max: End of the query window (defaults to 7 days from now).
        room_ids: Optional list of room IDs to filter. If None, queries all rooms.

    Returns:
        Dict mapping room_id to busy periods or None when Calendar cannot confirm.
    """
    now = datetime.now(timezone.utc)
    time_min = time_min or now
    time_max = time_max or now + timedelta(days=7)

    rooms = await get_all_rooms()
    selected = [room for room in rooms if room_ids is None or room["id"] in room_ids]
    # None means availability is unknown, which must never be treated as free.
    availability: dict[str, list[dict[str, str]] | None] = {
        room["id"]: None for room in selected
    }
    by_calendar: dict[str, list[dict]] = {}
    for room in rooms:
        calendar_id = calendar_id_for_room(room)
        if calendar_id:
            by_calendar.setdefault(calendar_id, []).append(room)
    selected_calendars = {calendar_id_for_room(room) for room in selected if calendar_id_for_room(room)}
    calendar_items = list(selected_calendars)
    if not calendar_items:
        return availability

    try:
        service = _get_calendar_service()
        shared = {
            cid: grouped for cid, grouped in by_calendar.items()
            if cid in selected_calendars and len(grouped) > 1
        }
        for calendar_id, grouped in shared.items():
            try:
                shared_availability = _query_shared_calendar(
                    service, calendar_id, grouped, time_min, time_max
                )
                availability.update({
                    room["id"]: shared_availability[room["id"]]
                    for room in selected if calendar_id_for_room(room) == calendar_id
                })
            except Exception:
                logger.exception("Failed to list events on shared calendar '%s'", calendar_id)

        single_calendar_items = [
            {"id": cid} for cid, grouped in by_calendar.items()
            if cid in selected_calendars and len(grouped) == 1
        ]
        if not single_calendar_items:
            return availability
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "timeZone": "Asia/Kolkata",
            "items": single_calendar_items,
        }

        result = service.freebusy().query(body=body).execute()
        calendars = result.get("calendars", {})

        for room in selected:
            cal_id = calendar_id_for_room(room)
            if cal_id in shared:
                continue
            data = calendars.get(cal_id) if cal_id else None
            if not data or data.get("errors"):
                continue
            availability[room["id"]] = [
                {"start": period["start"], "end": period["end"]}
                for period in data.get("busy", [])
            ]

        return availability

    except Exception:
        logger.exception("Failed to query Google Calendar FreeBusy API")
        return availability


async def create_calendar_event(
    room_id: str,
    title: str,
    start_time: datetime,
    end_time: datetime,
    description: str = "",
    attendee_email: str = "",
    booking_id: str = "",
) -> dict[str, Any] | None:
    """Create a calendar event on the specified room's configured calendar.

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
    room = await get_room_by_id(room_id)
    cal_id = calendar_id_for_room(room) if room else None
    if not room or room.get("booking_enabled") is False or not cal_id:
        logger.error("Cannot create calendar event: room '%s' has no calendar", room_id)
        return None
    room_name = room["name"]

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
        "location": room_name,
        "extendedProperties": {"private": {"room_id": room_id}},
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": "Asia/Kolkata",
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": "Asia/Kolkata",
        },
    }
    if booking_id:
        event_body["extendedProperties"]["private"]["booking_id"] = booking_id
        event_id = _event_id_for_booking(booking_id)
        if event_id:
            event_body["id"] = event_id
        else:
            logger.warning("Booking ID '%s' is not a UUID; Calendar will generate an event ID", booking_id)

    settings = get_settings()
    raw_override = getattr(settings, "notification_override_email", "")
    override_email = raw_override if isinstance(raw_override, str) else ""
    # Calendar rejects attendees for ordinary service-account access. Gmail
    # sends the separate booking notification when delegation is not enabled.
    supports_attendees = bool(settings.google_calendar_refresh_token or settings.google_calendar_subject)
    effective_attendee = (override_email or attendee_email) if supports_attendees else ""
    if effective_attendee:
        event_body["attendees"] = [{"email": effective_attendee}]

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
        if isinstance(e, HttpError) and e.resp.status == 409 and booking_id and event_body.get("id"):
            try:
                existing = service.events().get(calendarId=cal_id, eventId=event_body["id"]).execute()
                private = existing.get("extendedProperties", {}).get("private", {})
                if private.get("booking_id") == booking_id and private.get("room_id") == room_id:
                    return existing
            except Exception:
                logger.exception("Could not verify existing calendar event for booking %s", booking_id)
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
        logger.error("Failed to insert into calendar '%s': %s", cal_id, e)
        return None


async def delete_calendar_event(room_id: str, event_id: str) -> None:
    """Remove an event if approval lost a race to rejection."""
    room = await get_room_by_id(room_id)
    cal_id = calendar_id_for_room(room) if room else None
    if not cal_id:
        raise RuntimeError("Room calendar is not configured.")
    service = _get_calendar_service()
    service.events().delete(calendarId=cal_id, eventId=event_id).execute()


async def format_availability_for_llm(
    availability: dict[str, list[dict[str, str]] | None],
) -> str:
    """Format FreeBusy data as human-readable text for the LLM context window."""
    lines: list[str] = []
    rooms = await get_all_rooms()
    for room in rooms:
        room_id = room["id"]
        busy_slots = availability.get(room_id)
        lines.append(f"**{room['name']}** (ID: {room_id}):")
        if busy_slots is None:
            lines.append("  - Availability unknown; do not propose a booking for this room")
        elif not busy_slots:
            lines.append("  - No booking recorded on this calendar in the queried time window; physical room availability needs admin confirmation")
        else:
            for slot in busy_slots:
                lines.append(f"  - ❌ Busy: {slot['start']} → {slot['end']}")
        lines.append("")
    return "\n".join(lines)
