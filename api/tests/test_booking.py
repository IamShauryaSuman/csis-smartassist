import pytest
from unittest.mock import AsyncMock, patch, MagicMock



@pytest.fixture
def mock_extract_user():
    with patch("routes.booking._extract_user_id", return_value="test-user-id") as mock:
        yield mock

class MockTable:
    def __init__(self, name):
        self.name = name
        self._action = "select"
        self._single = False
    def select(self, *args, **kwargs):
        self._action = "select"
        return self
    def insert(self, *args, **kwargs):
        self._action = "insert"
        return self
    def update(self, *args, **kwargs):
        self._action = "update"
        return self
    def eq(self, *args, **kwargs): return self
    def order(self, *args, **kwargs): return self
    def single(self, *args, **kwargs):
        self._single = True
        return self
    def limit(self, *args, **kwargs): return self
    def execute(self):
        def format_data(items):
            return items[0] if self._single and items else items
            
        if self.name == "rooms":
            return MagicMock(data=format_data([{"name": "Test Room", "calendar_id": "cal-1"}]))
        if self.name == "profiles":
            return MagicMock(data=format_data([{"email": "admin@example.com", "full_name": "Test User"}]))
        if self.name == "bookings":
            return MagicMock(data=format_data([FULL_MOCK_BOOKING]))
        return MagicMock(data=format_data([]))

class MockSupabaseClient:
    def table(self, name):
        return MockTable(name)

@pytest.fixture
def mock_supabase():
    with patch("routes.booking.get_supabase_client") as mock_booking_db, \
         patch("core.rooms.get_supabase_client") as mock_rooms_db:
        client_mock = MockSupabaseClient()
        mock_booking_db.return_value = client_mock
        mock_rooms_db.return_value = client_mock
        yield client_mock

@pytest.fixture
def mock_admin_check():
    with patch("routes.booking._require_admin") as mock:
        yield mock


@pytest.fixture(autouse=True)
def mock_availability():
    async def available(_start, _end, room_ids):
        return {room_ids[0]: []}
    with patch("routes.booking.query_freebusy", side_effect=available) as mock:
        yield mock

FULL_MOCK_BOOKING = {
    "id": "b-1",
    "user_id": "test-user-id",
    "room_id": "r-1",
    "room_name": "Test Room",
    "title": "Test Booking",
    "description": "",
    "start_time": "2026-07-20T10:00:00Z",
    "end_time": "2026-07-20T11:00:00Z",
    "status": "pending",
    "admin_notes": "",
    "acknowledged_use": True,
    "acknowledged_availability": True,
    "is_locked": False,
    "locked_at": None,
    "locked_by": None,
    "created_at": "2026-07-15T00:00:00Z",
    "updated_at": "2026-07-15T00:00:00Z"
}

def test_get_bookings(client, mock_supabase, mock_extract_user):
    response = client.get("/api/bookings", headers={"Authorization": "Bearer token"})
    
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == "b-1"

def test_get_admin_bookings(client, mock_supabase, mock_extract_user, mock_admin_check):
    response = client.get("/api/bookings?all_users=true", headers={"Authorization": "Bearer token"})
    
    assert response.status_code == 200
    assert len(response.json()) == 1

@patch("routes.booking.send_admin_new_booking_notification", new_callable=AsyncMock)
def test_create_booking(mock_send_email, client, mock_supabase, mock_extract_user):
    response = client.post(
        "/api/bookings",
        headers={"Authorization": "Bearer test-token"},
        json={
            "room_id": "r-1",
            "room_name": "Test Room",
            "title": "Meeting",
            "description": "Desc",
            "start_time": "2026-07-20T10:00:00Z",
            "end_time": "2026-07-20T11:00:00Z",
            "acknowledged_use": True,
            "acknowledged_availability": True,
        }
    )
    
    assert response.status_code == 200
    assert response.json()["id"] == "b-1"
    # Ensure email background task was called
    assert mock_send_email.called

@patch("routes.booking.send_booking_notification", new_callable=AsyncMock)
@patch("routes.booking.create_calendar_event", new_callable=AsyncMock)
def test_admin_approve_booking(mock_calendar, mock_send_email, client, mock_supabase, mock_extract_user, mock_admin_check):
    mock_calendar.return_value = {"id": "event-id-123", "htmlLink": "https://calendar.google.com/event"}
    
    response = client.patch(
        "/api/bookings/b-1/status",
        headers={"Authorization": "Bearer admin-token"},
        json={"status": "approved", "admin_notes": "Looks good"}
    )
    
    assert response.status_code == 200
    assert mock_calendar.called
    assert mock_send_email.called

def test_lock_booking_success(client, mock_supabase, mock_extract_user):
    response = client.post("/api/bookings/b-123/lock", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200

def test_lock_booking_not_owner(client, mock_supabase, mock_extract_user):
    class OtherUserMockTable(MockTable):
        def execute(self):
            items = [{**FULL_MOCK_BOOKING, "user_id": "other-user-id"}]
            data = items[0] if self._single else items
            return MagicMock(data=data)
    
    mock_supabase.table = lambda name: OtherUserMockTable(name)
    response = client.post("/api/bookings/b-123/lock", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 403

def test_edit_booking_without_lock(client, mock_supabase, mock_extract_user):
    response = client.patch("/api/bookings/b-123", headers={"Authorization": "Bearer test-token"}, json={
        "room_id": "dlt_8", "room_name": "DLT-8", "title": "New Title", "description": "", 
        "start_time": "2026-07-13T10:00:00Z", "end_time": "2026-07-13T11:00:00Z",
        "acknowledged_use": True, "acknowledged_availability": True,
    })
    assert response.status_code == 403

def test_unlock_booking(client, mock_supabase, mock_extract_user):
    response = client.post("/api/bookings/b-123/unlock", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200

@patch("routes.booking.send_booking_notification", new_callable=AsyncMock)
def test_admin_reject_booking(mock_send_email, client, mock_supabase, mock_extract_user, mock_admin_check):
    response = client.patch(
        "/api/bookings/b-1/status",
        headers={"Authorization": "Bearer admin-token"},
        json={"status": "rejected", "admin_notes": "Not allowed"}
    )
    
    assert response.status_code == 200
    assert mock_send_email.called

def test_create_booking_invalid_time(client, mock_supabase, mock_extract_user):
    response = client.post(
        "/api/bookings",
        headers={"Authorization": "Bearer test-token"},
        json={
            "room_id": "r-1",
            "room_name": "Test Room",
            "title": "Meeting",
            "description": "Desc",
            "start_time": "2026-07-20T11:00:00Z",  # End is before start
            "end_time": "2026-07-20T10:00:00Z",
            "acknowledged_use": True,
            "acknowledged_availability": True,
        }
    )
    
    assert response.status_code == 400
    assert "End time must be after" in response.json()["detail"]

def test_update_booking_details_success(client, mock_supabase, mock_extract_user):
    class LockedMockTable(MockTable):
        def execute(self):
            items = [{**FULL_MOCK_BOOKING, "is_locked": True, "locked_by": "test-user-id"}]
            data = items[0] if self._single else items
            return MagicMock(data=data)
    
    mock_supabase.table = lambda name: LockedMockTable(name) if name == "bookings" else MockTable(name)
    response = client.patch("/api/bookings/b-123", headers={"Authorization": "Bearer test-token"}, json={
        "room_id": "dlt_8", "room_name": "DLT-8", "title": "New Title", "description": "", 
        "start_time": "2026-07-13T10:00:00Z", "end_time": "2026-07-13T11:00:00Z",
        "acknowledged_use": True, "acknowledged_availability": True,
    })
    
    assert response.status_code == 200
    assert response.json()["is_locked"] == True # Wait, response returns data directly, but wait, the endpoint returns result.data[0] from update!
    # With MockTable, update returns MagicMock(data=items) from execute.
    # Since it's returning FULL_MOCK_BOOKING, we just check status 200.


def test_create_booking_requires_acknowledgements(client, mock_supabase, mock_extract_user):
    response = client.post("/api/bookings", headers={"Authorization": "Bearer token"}, json={
        "room_id": "r-1", "room_name": "Test Room", "title": "Meeting",
        "start_time": "2026-10-01T10:00:00+05:30",
        "end_time": "2026-10-01T11:00:00+05:30",
    })
    assert response.status_code == 400
    assert "acknowledgements" in response.json()["detail"]


def test_create_booking_rejects_disabled_general_classroom(client, mock_supabase, mock_extract_user):
    with patch("routes.booking.get_room_by_id", new_callable=AsyncMock) as room_lookup:
        room_lookup.return_value = {
            "id": "d_153", "name": "D-153", "booking_enabled": False,
            "is_general_classroom": True,
        }
        response = client.post("/api/bookings", headers={"Authorization": "Bearer token"}, json={
            "room_id": "d_153", "room_name": "D-153", "title": "Study",
            "start_time": "2026-10-01T10:00:00+05:30",
            "end_time": "2026-10-01T11:00:00+05:30",
            "acknowledged_use": True, "acknowledged_availability": True,
        })
    assert response.status_code == 400
    assert response.json()["detail"] == "This room is not open for booking."


def test_create_booking_does_not_assume_unavailable_calendar_is_free(
    client, mock_supabase, mock_extract_user, mock_availability,
):
    mock_availability.side_effect = lambda _start, _end, _ids: {"r-1": None}
    response = client.post("/api/bookings", headers={"Authorization": "Bearer token"}, json={
        "room_id": "r-1", "room_name": "Test Room", "title": "Meeting",
        "start_time": "2026-10-01T10:00:00+05:30",
        "end_time": "2026-10-01T11:00:00+05:30",
        "acknowledged_use": True, "acknowledged_availability": True,
    })
    assert response.status_code == 503


def test_create_booking_rejects_busy_room(client, mock_supabase, mock_extract_user, mock_availability):
    mock_availability.side_effect = lambda _start, _end, _ids: {"r-1": [{
        "start": "2026-10-01T10:30:00+05:30", "end": "2026-10-01T11:30:00+05:30",
    }]}
    response = client.post("/api/bookings", headers={"Authorization": "Bearer token"}, json={
        "room_id": "r-1", "room_name": "Test Room", "title": "Meeting",
        "start_time": "2026-10-01T10:00:00+05:30",
        "end_time": "2026-10-01T11:00:00+05:30",
        "acknowledged_use": True, "acknowledged_availability": True,
    })
    assert response.status_code == 409


@patch("routes.booking.send_booking_notification", new_callable=AsyncMock)
@patch("routes.booking.create_calendar_event", new_callable=AsyncMock, return_value=None)
def test_approval_stays_pending_when_event_fails(
    mock_calendar, mock_email, client, mock_supabase, mock_extract_user, mock_admin_check,
):
    response = client.patch("/api/bookings/b-1/status", headers={"Authorization": "Bearer token"},
                            json={"status": "approved"})
    assert response.status_code == 503
    assert mock_calendar.called
    mock_email.assert_not_called()


@patch("routes.booking.send_booking_notification", new_callable=AsyncMock)
@patch("routes.booking.create_calendar_event", new_callable=AsyncMock)
@patch("routes.booking.get_calendar_event_for_booking", new_callable=AsyncMock)
def test_approval_can_resume_after_calendar_event_exists(
    mock_existing, mock_create, mock_email, client, mock_supabase,
    mock_extract_user, mock_admin_check, mock_availability,
):
    mock_existing.return_value = {"id": "existing-event", "htmlLink": "https://calendar.google.com/event"}
    mock_availability.side_effect = lambda _start, _end, _ids: {"r-1": [{
        "start": "2026-07-20T10:00:00Z", "end": "2026-07-20T11:00:00Z",
    }]}
    response = client.patch("/api/bookings/b-1/status", headers={"Authorization": "Bearer token"},
                            json={"status": "approved"})
    assert response.status_code == 200
    mock_create.assert_not_called()
    mock_availability.assert_not_called()
