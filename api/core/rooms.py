"""
Dynamic room/lab manifest for the CSIS Department.

This module fetches all bookable spaces with their capabilities,
hardware profiles, and capacity from the database. The LLM uses this to suggest alternatives
when a requested room is unavailable.

Each room's `calendar_id` maps to a Google Calendar resource.
"""

from __future__ import annotations

from core.database import get_supabase_client
from core.config import get_settings


def calendar_id_for_room(room: dict) -> str | None:
    """Use a room calendar when present, otherwise the configured shared calendar."""
    return room.get("calendar_id") or get_settings().google_calendar_id or None

async def get_all_rooms() -> list[dict]:
    """Fetch rooms enabled for new booking proposals."""
    supabase = get_supabase_client()
    response = supabase.table("rooms").select("*").eq("booking_enabled", True).execute()
    return response.data

async def get_rooms_manifest_text() -> str:
    """Format the rooms manifest as a human-readable text block for LLM context."""
    rooms = await get_all_rooms()
    lines: list[str] = []
    for room in rooms:
        lines.append(f"### {room['name']} (ID: {room['id']})")
        lines.append(f"- **Type:** {room['type'].replace('_', ' ').title()}")
        capacity = room.get("capacity")
        lines.append(f"- **Capacity:** {capacity} seats" if capacity else "- **Capacity:** Not confirmed")
        
        hardware = room.get('hardware', [])
        lines.append(f"- **Hardware:** {', '.join(hardware)}")
        
        desc = room.get('description') or ''
        lines.append(f"- **Description:** {desc}")
        lines.append("")
    return "\n".join(lines)

async def get_room_by_id(room_id: str) -> dict | None:
    """Look up a room by its ID from the database."""
    supabase = get_supabase_client()
    response = supabase.table("rooms").select("*").eq("id", room_id).execute()
    data = response.data
    if data and len(data) > 0:
        return data[0]
    return None

async def get_calendar_ids() -> list[str]:
    """Return all Google Calendar IDs for FreeBusy queries."""
    rooms = await get_all_rooms()
    return list({calendar_id_for_room(room) for room in rooms if calendar_id_for_room(room)})
