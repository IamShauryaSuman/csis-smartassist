"""
Booking router — Space reservation CRUD and admin approval workflow.

Handles booking creation from chat proposals, user booking history,
and admin status management with email notifications.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from core.config import get_settings
from core.database import get_supabase_client
from core.rooms import get_all_rooms, get_room_by_id
from services.calendar import create_calendar_event
from services.gmail import send_admin_new_booking_notification, send_booking_notification

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bookings", tags=["bookings"])


# ── Request / Response Models ───────────────────────────────────────────────


class CreateBookingRequest(BaseModel):
    room_id: str
    room_name: str
    title: str
    description: str = ""
    start_time: str  # ISO 8601
    end_time: str  # ISO 8601


class UpdateBookingRequest(BaseModel):
    status: str  # 'approved' or 'rejected'
    admin_notes: str = ""


class BookingResponse(BaseModel):
    id: str
    user_id: str
    room_id: str
    room_name: str
    title: str
    description: str | None
    start_time: str
    end_time: str
    status: str
    admin_notes: str | None
    is_locked: bool = False
    locked_at: str | None = None
    locked_by: str | None = None
    created_at: str
    updated_at: str


# ── Auth Helper ─────────────────────────────────────────────────────────────


def _extract_user_id(authorization: str) -> str:
    """Validate JWT and extract user ID."""
    settings = get_settings()
    if getattr(settings, "local_auth", False):
        return getattr(settings, "local_user_id", "1cce9d10-6970-4c9f-9f5e-39bc6b6c6671")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header.")

    token = authorization.removeprefix("Bearer ").strip()
    db = get_supabase_client()
    try:
        user_response = db.auth.get_user(token)
        return user_response.user.id
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")


def _require_admin(user_id: str) -> None:
    """Verify the user has admin privileges."""
    settings = get_settings()
    if getattr(settings, "local_auth", False):
        return  # In local mode, admin access is granted

    db = get_supabase_client()
    profile = (
        db.table("profiles")
        .select("is_admin")
        .eq("id", user_id)
        .single()
        .execute()
    )
    if not profile.data or not profile.data.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")


# ── Booking Endpoints ───────────────────────────────────────────────────────


@router.post("", response_model=BookingResponse)
async def create_booking(
    body: CreateBookingRequest,
    authorization: str = Header(...),
):
    """Create a new booking request (status: pending).

    Typically triggered when a user confirms a booking_proposal from the chat.
    """
    user_id = _extract_user_id(authorization)
    db = get_supabase_client()

    # Validate room exists
    room = await get_room_by_id(body.room_id)
    if not room:
        raise HTTPException(status_code=400, detail=f"Unknown room ID: {body.room_id}")

    # Validate time range
    try:
        start = datetime.fromisoformat(body.start_time)
        end = datetime.fromisoformat(body.end_time)
        if end <= start:
            raise ValueError("End time must be after start time.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid time range: {e}")

    # Expire any existing pending bookings for this user that overlap in time
    # This ensures a user can reschedule by simply creating a new booking for the same slot.
    overlapping = (
        db.table("bookings")
        .select("id, start_time, end_time")
        .eq("user_id", user_id)
        .eq("status", "pending")
        .execute()
    )
    
    for b in overlapping.data:
        b_start = datetime.fromisoformat(b["start_time"])
        b_end = datetime.fromisoformat(b["end_time"])
        
        # Overlap condition: max(start1, start2) < min(end1, end2)
        if max(start, b_start) < min(end, b_end):
            db.table("bookings").update({"status": "expired"}).eq("id", b["id"]).execute()

    result = (
        db.table("bookings")
        .insert({
            "user_id": user_id,
            "room_id": body.room_id,
            "room_name": body.room_name or room["name"],
            "title": body.title,
            "description": body.description,
            "start_time": body.start_time,
            "end_time": body.end_time,
            "status": "pending",
        })
        .execute()
    )
    booking_data = result.data[0]

    # Fetch requester name
    requester = (
        db.table("profiles")
        .select("full_name")
        .eq("id", user_id)
        .single()
        .execute()
    )
    requester_name = requester.data.get("full_name", "A student") if requester.data else "A student"

    # Fetch all admins to notify
    admins = (
        db.table("profiles")
        .select("email")
        .eq("is_admin", True)
        .execute()
    )
    admin_emails = [admin["email"] for admin in admins.data if admin.get("email")]

    if admin_emails:
        asyncio.create_task(
            send_admin_new_booking_notification(
                admin_emails=admin_emails,
                requester_name=requester_name,
                booking_title=body.title,
                description=body.description or "",
                room_name=body.room_name or room["name"],
                start_time=body.start_time,
                end_time=body.end_time,
            )
        )

    return booking_data


@router.get("", response_model=list[BookingResponse])
async def list_bookings(
    authorization: str = Header(...),
    status: str | None = Query(None, description="Filter by status"),
    all_users: bool = Query(False, description="Admin: list all users' bookings"),
):
    """List bookings for the authenticated user (or all, for admins)."""
    user_id = _extract_user_id(authorization)
    db = get_supabase_client()

    query = db.table("bookings").select("*")

    if all_users:
        _require_admin(user_id)
    else:
        query = query.eq("user_id", user_id)

    if status:
        query = query.eq("status", status)

    result = query.order("created_at", desc=True).execute()
    return result.data


@router.get("/{booking_id}", response_model=BookingResponse)
async def get_booking(
    booking_id: str,
    authorization: str = Header(...),
):
    """Get a single booking by ID."""
    user_id = _extract_user_id(authorization)
    db = get_supabase_client()

    result = (
        db.table("bookings")
        .select("*")
        .eq("id", booking_id)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Booking not found.")

    # Allow access if owner or admin
    if result.data["user_id"] != user_id:
        _require_admin(user_id)

    return result.data


@router.post("/{booking_id}/lock", response_model=BookingResponse)
async def lock_booking(
    booking_id: str,
    authorization: str = Header(...),
):
    """Lock a pending booking for editing by the user."""
    user_id = _extract_user_id(authorization)
    db = get_supabase_client()

    booking_result = db.table("bookings").select("*").eq("id", booking_id).single().execute()
    if not booking_result.data:
        raise HTTPException(status_code=404, detail="Booking not found.")

    booking = booking_result.data
    if booking["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You can only lock your own bookings.")

    if booking["status"] != "pending":
        raise HTTPException(status_code=400, detail="Only pending bookings can be locked/edited.")

    # Even if it's already locked by someone else (or stuck), the user who owns it should be able to claim/refresh the lock.
    update_data = {
        "is_locked": True,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "locked_by": user_id,
    }
    result = db.table("bookings").update(update_data).eq("id", booking_id).execute()
    return result.data[0]


@router.post("/{booking_id}/unlock", response_model=BookingResponse)
async def unlock_booking(
    booking_id: str,
    authorization: str = Header(...),
):
    """Unlock a booking."""
    user_id = _extract_user_id(authorization)
    db = get_supabase_client()

    booking_result = db.table("bookings").select("*").eq("id", booking_id).single().execute()
    if not booking_result.data:
        raise HTTPException(status_code=404, detail="Booking not found.")

    booking = booking_result.data
    
    # Allow unlock if user owns it, or if admin.
    if booking["user_id"] != user_id:
        _require_admin(user_id)

    update_data = {
        "is_locked": False,
        "locked_at": None,
        "locked_by": None,
    }
    result = db.table("bookings").update(update_data).eq("id", booking_id).execute()
    return result.data[0]


@router.patch("/{booking_id}", response_model=BookingResponse)
async def update_booking_details(
    booking_id: str,
    body: CreateBookingRequest,
    authorization: str = Header(...),
):
    """Update a pending booking details. Must be locked by the user first."""
    user_id = _extract_user_id(authorization)
    db = get_supabase_client()

    booking_result = db.table("bookings").select("*").eq("id", booking_id).single().execute()
    if not booking_result.data:
        raise HTTPException(status_code=404, detail="Booking not found.")

    booking = booking_result.data
    if booking["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You can only edit your own bookings.")
        
    if booking["status"] != "pending":
        raise HTTPException(status_code=400, detail="Only pending bookings can be edited.")
        
    if not booking.get("is_locked") or booking.get("locked_by") != user_id:
        raise HTTPException(status_code=403, detail="Booking must be locked by you before editing.")

    # Validate time range
    try:
        start = datetime.fromisoformat(body.start_time)
        end = datetime.fromisoformat(body.end_time)
        if end <= start:
            raise ValueError("End time must be after start time.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid time range: {e}")

    # Update the details AND unlock it
    update_data = {
        "room_id": body.room_id,
        "room_name": body.room_name,
        "title": body.title,
        "description": body.description,
        "start_time": body.start_time,
        "end_time": body.end_time,
        "is_locked": False,
        "locked_at": None,
        "locked_by": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    result = db.table("bookings").update(update_data).eq("id", booking_id).execute()
    return result.data[0]


@router.patch("/{booking_id}/status", response_model=BookingResponse)
async def update_booking_status(
    booking_id: str,
    body: UpdateBookingRequest,
    authorization: str = Header(...),
):
    """Admin endpoint: approve or reject a booking request.

    On approval, creates a Google Calendar event on the room's calendar
    and sends an email notification to the requester.
    """
    user_id = _extract_user_id(authorization)
    _require_admin(user_id)
    db = get_supabase_client()

    if body.status not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400, detail="Status must be 'approved' or 'rejected'."
        )

    # Fetch the booking
    booking_result = (
        db.table("bookings")
        .select("*")
        .eq("id", booking_id)
        .single()
        .execute()
    )
    if not booking_result.data:
        raise HTTPException(status_code=404, detail="Booking not found.")

    booking = booking_result.data

    if booking["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Booking already {booking['status']}. Cannot modify.",
        )
        
    if booking.get("is_locked"):
        raise HTTPException(
            status_code=409,
            detail="Booking is currently locked by the user for editing. Please try again later.",
        )

    # Update status
    update_data: dict[str, Any] = {
        "status": body.status,
        "admin_notes": body.admin_notes,
    }
    result = (
        db.table("bookings")
        .update(update_data)
        .eq("id", booking_id)
        .execute()
    )

    updated_booking = result.data[0]

    # ── Post-approval actions ───────────────────────────────────────────────
    # Fetch requester profile for notifications and calendar invitations
    requester_email = ""
    requester_name = "Student"
    requester = (
        db.table("profiles")
        .select("email, full_name")
        .eq("id", booking["user_id"])
        .single()
        .execute()
    )
    if requester.data:
        requester_email = requester.data.get("email", "")
        requester_name = requester.data.get("full_name", "Student")

    if body.status == "approved":
        # Create Google Calendar event
        try:
            cal_event = await create_calendar_event(
                room_id=booking["room_id"],
                title=f"[{booking.get('room_name') or 'Booking'}] {booking['title']}",
                start_time=datetime.fromisoformat(booking["start_time"]),
                end_time=datetime.fromisoformat(booking["end_time"]),
                description=(
                    f"Booked by: {requester_name} ({requester_email})\n\n"
                    f"Room: {booking.get('room_name') or booking['room_id']}\n"
                    f"Description: {booking.get('description', '')}\n"
                    f"Admin notes: {body.admin_notes or 'None'}"
                ),
                attendee_email=requester_email,
            )
            if cal_event:
                logger.info("Successfully created calendar event for booking %s: %s", booking_id, cal_event.get("htmlLink"))
            else:
                logger.warning("Calendar event creation returned None for booking %s", booking_id)
        except Exception:
            logger.exception("Failed to create calendar event for booking %s", booking_id)

    # Send email notification
    if requester_email:
        try:
            await send_booking_notification(
                to_email=requester_email,
                user_name=requester_name,
                booking_title=booking["title"],
                description=booking.get("description", ""),
                room_name=booking["room_name"],
                start_time=booking["start_time"],
                end_time=booking["end_time"],
                status=body.status,
                admin_notes=body.admin_notes,
            )
        except Exception:
            logger.exception("Failed to send booking notification for %s", booking_id)

    return updated_booking


# ── Room Availability Endpoint ──────────────────────────────────────────────


@router.get("/rooms/list")
async def list_rooms(authorization: str = Header(...)):
    """Return the full rooms manifest."""
    _extract_user_id(authorization)  # Ensure authenticated
    return await get_all_rooms()


# ── Diagnostic Test Endpoints ────────────────────────────────────────────────


@router.post("/test-email")
async def test_email_endpoint(
    target_email: str = Query(..., description="Email address to receive the test notification"),
    authorization: str = Header(...),
):
    """Test sending an email notification via Gmail API."""
    _extract_user_id(authorization)
    try:
        success = await send_booking_notification(
            to_email=target_email,
            user_name="Test User",
            booking_title="Test Room Booking Notification",
            description="This is a test booking notification to verify Gmail API delivery.",
            room_name="CSIS Lab 1 (CC-101)",
            start_time="2026-09-12 10:00 AM",
            end_time="2026-09-12 12:00 PM",
            status="approved",
            admin_notes="Verification test initiated via /api/bookings/test-email.",
            raise_exception=True,
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to send test email. Check server logs.")
        return {"status": "success", "sent_to": target_email}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send test email: {type(e).__name__}: {str(e)}")



@router.post("/test-calendar")
async def test_calendar_endpoint(
    room_id: str = Query("dlt_8", description="Room ID to test (e.g. dlt_8, lab_1)"),
    authorization: str = Header(...),
):
    """Test inserting a test event into Google Calendar."""
    _extract_user_id(authorization)
    from datetime import timedelta
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist_tz)
    cal_event = await create_calendar_event(
        room_id=room_id,
        title=f"[Test] SmartAssist Calendar Check ({room_id})",
        start_time=now + timedelta(hours=1),
        end_time=now + timedelta(hours=2),
        description=f"Automated diagnostic test event from CSIS SmartAssist for room {room_id}.",
    )
    if not cal_event:
        raise HTTPException(
            status_code=500,
            detail="Failed to create calendar event. Check container logs (`podman logs smartassist-api`) for details.",
        )
    return {
        "status": "success",
        "room_id": room_id,
        "event_id": cal_event.get("id"),
        "html_link": cal_event.get("htmlLink"),
        "calendar_organizer": cal_event.get("organizer", {}).get("email"),
    }

