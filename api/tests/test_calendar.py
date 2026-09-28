import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from services.calendar import _get_calendar_service, create_calendar_event, query_freebusy, format_availability_for_llm

@pytest.fixture
def mock_credentials():
    with patch("services.calendar.service_account.Credentials.from_service_account_info") as mock:
        mock.return_value = MagicMock()
        yield mock

@pytest.fixture
def mock_build():
    with patch("services.calendar.build") as mock:
        service_mock = MagicMock()
        mock.return_value = service_mock
        yield service_mock

def test_get_calendar_service_missing_env():
    with patch("services.calendar.get_settings") as mock_settings:
        mock_settings.return_value.google_calendar_refresh_token = ""
        mock_settings.return_value.google_service_account_info = None
        with pytest.raises(RuntimeError, match="Google Calendar credentials not configured."):
            _get_calendar_service()


def test_get_calendar_service_uses_account_oauth_when_configured(mock_build):
    with patch("services.calendar.get_settings") as mock_settings, \
         patch("services.calendar.UserCredentials") as mock_user_credentials:
        settings = mock_settings.return_value
        settings.google_calendar_refresh_token = "calendar-refresh-token"
        settings.google_calendar_client_id = "client-id"
        settings.google_calendar_client_secret = "client-secret"
        _get_calendar_service()
    mock_user_credentials.assert_called_once_with(
        token=None,
        refresh_token="calendar-refresh-token",
        client_id="client-id",
        client_secret="client-secret",
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/calendar"],
    )

@pytest.mark.asyncio
async def test_create_calendar_event(mock_credentials, mock_build):
    events_mock = mock_build.events.return_value.insert.return_value
    events_mock.execute.return_value = {"id": "new_event_123", "htmlLink": "http://link"}
    
    with patch("services.calendar._get_calendar_service", return_value=mock_build), \
         patch("services.calendar.get_room_by_id", return_value={"id": "test_cal_id", "name": "Room", "calendar_id": "real_cal_id"}):
        from datetime import datetime
        event = await create_calendar_event(
            "test_cal_id",
            "Test Event",
            datetime(2023, 1, 1, 10),
            datetime(2023, 1, 1, 11),
            "Test Description",
            "test@example.com",
            "123e4567-e89b-12d3-a456-426614174000",
        )
    assert event is not None
    assert event["id"] == "new_event_123"
    mock_build.events.return_value.insert.assert_called_once()
    insert_kwargs = mock_build.events.return_value.insert.call_args.kwargs
    assert insert_kwargs["calendarId"] == "real_cal_id"
    assert insert_kwargs["body"]["id"] == "b123e4567e89b12d3a456426614174000"
    assert insert_kwargs["body"]["extendedProperties"]["private"]["room_id"] == "test_cal_id"


@pytest.mark.asyncio
async def test_create_calendar_event_skips_disabled_room(mock_build):
    with patch("services.calendar.get_room_by_id", return_value={
        "id": "d_153", "name": "D-153", "calendar_id": "shared",
        "booking_enabled": False,
    }):
        from datetime import datetime
        event = await create_calendar_event(
            "d_153", "Study", datetime(2026, 10, 1, 10), datetime(2026, 10, 1, 11)
        )
    assert event is None
    mock_build.events.return_value.insert.assert_not_called()

@pytest.mark.asyncio
async def test_query_freebusy(mock_credentials, mock_build):
    mock_build.freebusy.return_value.query.return_value.execute.return_value = {
        "calendars": {
            "real_cal_id": {"busy": [{"start": "2023-01-01T10:00:00Z", "end": "2023-01-01T11:00:00Z"}]}
        }
    }
    
    with patch("services.calendar._get_calendar_service", return_value=mock_build), \
         patch("services.calendar.get_all_rooms", return_value=[{"id": "cal_1", "calendar_id": "real_cal_id"}]):
        from datetime import datetime
        busy_data = await query_freebusy(
            datetime(2023, 1, 1),
            datetime(2023, 1, 2)
        )
    assert "cal_1" in busy_data
    assert len(busy_data["cal_1"]) == 1


@pytest.mark.asyncio
async def test_query_freebusy_marks_calendar_errors_unknown(mock_build):
    mock_build.freebusy.return_value.query.return_value.execute.return_value = {
        "calendars": {"real_cal_id": {"errors": [{"reason": "notFound"}]}}
    }
    with patch("services.calendar._get_calendar_service", return_value=mock_build), \
         patch("services.calendar.get_all_rooms", return_value=[
             {"id": "room_1", "name": "Room 1", "calendar_id": "real_cal_id"}
         ]):
        availability = await query_freebusy(room_ids=["room_1"])
        text = await format_availability_for_llm(availability)
    assert availability["room_1"] is None
    assert "Availability unknown" in text


@pytest.mark.asyncio
async def test_query_freebusy_marks_unconfigured_room_unknown():
    with patch("services.calendar.get_all_rooms", return_value=[
        {"id": "room_1", "name": "Room 1", "calendar_id": None}
    ]), patch("core.rooms.get_settings", return_value=MagicMock(google_calendar_id="")):
        assert await query_freebusy(room_ids=["room_1"]) == {"room_1": None}


@pytest.mark.asyncio
async def test_shared_calendar_keeps_rooms_separate(mock_build):
    rooms = [
        {"id": "d153", "name": "D-153", "calendar_id": "shared"},
        {"id": "conference", "name": "CSIS Conference Room", "calendar_id": "shared"},
    ]
    mock_build.events.return_value.list.return_value.execute.return_value = {
        "items": [{
            "summary": "[D-153] Lecture",
            "start": {"dateTime": "2026-10-01T10:00:00+05:30"},
            "end": {"dateTime": "2026-10-01T11:00:00+05:30"},
            "extendedProperties": {"private": {"room_id": "d153"}},
        }]
    }
    with patch("services.calendar._get_calendar_service", return_value=mock_build), \
         patch("services.calendar.get_all_rooms", return_value=rooms):
        availability = await query_freebusy(room_ids=["conference"])
    assert availability == {"conference": []}
    mock_build.freebusy.return_value.query.assert_not_called()


@pytest.mark.asyncio
async def test_unidentified_event_on_shared_calendar_is_unknown(mock_build):
    rooms = [
        {"id": "d153", "name": "D-153", "calendar_id": "shared"},
        {"id": "conference", "name": "CSIS Conference Room", "calendar_id": "shared"},
    ]
    mock_build.events.return_value.list.return_value.execute.return_value = {
        "items": [{
            "summary": "Unidentified reservation",
            "start": {"dateTime": "2026-10-01T10:00:00+05:30"},
            "end": {"dateTime": "2026-10-01T11:00:00+05:30"},
        }]
    }
    with patch("services.calendar._get_calendar_service", return_value=mock_build), \
         patch("services.calendar.get_all_rooms", return_value=rooms):
        availability = await query_freebusy()
    assert availability == {"d153": None, "conference": None}


@pytest.mark.asyncio
async def test_shared_calendar_classifies_existing_event_by_location(mock_build):
    rooms = [
        {"id": "d153", "name": "D-153", "calendar_id": "shared"},
        {"id": "conference", "name": "CSIS Conference Room", "calendar_id": "shared"},
    ]
    mock_build.events.return_value.list.return_value.execute.return_value = {
        "items": [{
            "summary": "Faculty meeting",
            "location": "CSIS Conference Room",
            "start": {"dateTime": "2026-10-01T10:00:00+05:30"},
            "end": {"dateTime": "2026-10-01T11:00:00+05:30"},
        }]
    }
    with patch("services.calendar._get_calendar_service", return_value=mock_build), \
         patch("services.calendar.get_all_rooms", return_value=rooms):
        availability = await query_freebusy()
    assert availability["d153"] == []
    assert availability["conference"] == [{
        "start": "2026-10-01T10:00:00+05:30",
        "end": "2026-10-01T11:00:00+05:30",
    }]

@pytest.mark.asyncio
async def test_format_availability_for_llm(mock_credentials, mock_build):
    availability = {
        "room_1": [{"start": "2023-01-01T10:00:00Z", "end": "2023-01-01T11:00:00Z"}]
    }
    
    with patch("services.calendar.get_all_rooms", return_value=[{"id": "room_1", "name": "Room 1", "calendar_id": "cal_1", "type": "computer_lab"}]):
        text = await format_availability_for_llm(availability)
        
    assert "Room 1" in text
    assert "2023-01-01T10:00:00Z → 2023-01-01T11:00:00Z" in text


@pytest.mark.asyncio
async def test_empty_calendar_does_not_claim_physical_availability():
    with patch("services.calendar.get_all_rooms", return_value=[
        {"id": "conference", "name": "CSIS Conference Room"}
    ]):
        text = await format_availability_for_llm({"conference": []})
    assert "No booking recorded" in text
    assert "physical room availability needs admin confirmation" in text
