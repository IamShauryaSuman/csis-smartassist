-- Stage the first two requested spaces without inventing capacity or calendar IDs.
-- Set GOOGLE_CALENDAR_ID to use one shared calendar, or set a room-specific
-- calendar_id after the calendar/timetable source is confirmed.
ALTER TABLE public.rooms ALTER COLUMN calendar_id DROP NOT NULL;
ALTER TABLE public.rooms ALTER COLUMN capacity DROP NOT NULL;
ALTER TABLE public.rooms
    ADD COLUMN IF NOT EXISTS is_general_classroom BOOLEAN NOT NULL DEFAULT FALSE;
-- Earlier migrations contain illustrative rooms and calendar IDs. Keep those
-- out of the live booking flow until each room and its timetable are verified.
ALTER TABLE public.rooms
    ADD COLUMN IF NOT EXISTS booking_enabled BOOLEAN NOT NULL DEFAULT FALSE;

INSERT INTO public.rooms
    (id, name, type, capacity, hardware, calendar_id, description, is_general_classroom, booking_enabled)
VALUES
    ('d_153', 'D-153', 'classroom', NULL, '{}', NULL,
     'Classroom D-153. General classroom; approval confirms room use.', TRUE, TRUE),
    ('csis_conference_room', 'CSIS Conference Room', 'meeting_room', NULL, '{}', NULL,
     'CSIS Conference Room. Calendar shows recorded bookings; approval confirms room use.', FALSE, TRUE)
ON CONFLICT (id) DO NOTHING;

UPDATE public.rooms SET booking_enabled = TRUE WHERE id = 'csis_conference_room';
UPDATE public.rooms SET booking_enabled = TRUE, is_general_classroom = TRUE WHERE id = 'd_153';

ALTER TABLE public.bookings
    ADD COLUMN IF NOT EXISTS acknowledged_use BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.bookings
    ADD COLUMN IF NOT EXISTS acknowledged_availability BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.bookings
    ADD COLUMN IF NOT EXISTS calendar_event_id TEXT;
ALTER TABLE public.bookings
    ADD COLUMN IF NOT EXISTS calendar_event_link TEXT;
