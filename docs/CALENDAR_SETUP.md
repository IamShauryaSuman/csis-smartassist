# Room calendar setup

The first enabled room is the **CSIS Conference Room**. D-153 is staged in the
database but disabled while general classrooms are out of scope. The older room
rows in the initial seed data are also disabled until their details and calendar
sources are verified.

Use the primary Google Calendar of `smartassist.csis@gmail.com` to track room
bookings created by SmartAssist. This calendar has no confirmed class or room
timetable. An empty slot means only **no booking is recorded on this calendar**;
the admin must confirm actual room availability before approval.

## Connect the account

1. Apply `supabase/migrations/007_priority_classrooms.sql` to the project's
   Supabase database. It adds room enablement, the priority room rows, booking
   acknowledgements, and Calendar event tracking fields.
2. In the team's Google Cloud project, enable the Google Calendar API, configure
   OAuth consent for `smartassist.csis@gmail.com`, and create a **Desktop app**
   OAuth client. Download its JSON file. Google's [Python quickstart](https://developers.google.com/workspace/calendar/api/quickstart/python)
   walks through those console steps. Keep this file outside the repository.
3. From the repository root, use a Python 3.12 environment with the API
   dependencies installed, then run:

   ```sh
   python3.12 -m venv .venv
   .venv/bin/pip install -r api/requirements.txt
   .venv/bin/python scripts/connect_calendar.py --client-secrets /absolute/path/to/oauth-client.json
   ```

   Sign in as `smartassist.csis@gmail.com` in the browser window. The helper
   checks that account's primary calendar and saves the OAuth client ID,
   secret, and Calendar refresh token in the ignored root `.env` file. It never
   prints the token. Do not paste the account password or tokens into chat.
4. After account verification, set `GOOGLE_CALENDAR_ID=primary` in `.env` and
   restart the API. Copy these four Calendar variables to the API host's secret
   configuration for deployment; the local `.env` is not deployed. The Gmail
   sending token is separate and cannot be assumed to have Calendar permission.
5. Verify a request and admin approval with a real conference-room slot. The
   approval should create an event on the account calendar, and the booking
   should store its Calendar event ID and link. Recheck a conflicting slot.

If the OAuth consent project is **External / Testing**, Google says its offline
refresh token expires after seven days. Plan the project's production OAuth
configuration before relying on this connection long term; see Google's
[audience guidance](https://support.google.com/cloud/answer/15549945?hl=en).

## Calendar behavior

SmartAssist writes approved bookings to the shared calendar with private room
and booking IDs. A booking remains pending if Calendar cannot be checked or the
event cannot be created. Existing calendar events can also be matched by an
unambiguous room name or ID in their title or location. An event with no clear
room association makes availability unknown for the rooms sharing that calendar.

The booking form asks users to confirm academic/study use and acknowledge that
the calendar contains recorded bookings only. The API checks both acknowledgements
and checks the slot again when an admin approves it. No recurring bookings are
created in this phase.

## Later rooms

Keep `booking_enabled = false` until each room's name, classification, schedule
source, and policy are confirmed. `calendar_id = NULL` uses the shared calendar.
Set `booking_enabled = true` only when the room can be safely offered in the
booking flow. Capacity and hardware may remain unset until verified.

For a general classroom such as D-153, confirm college days and hours before
enabling it, then set these server-side values (Asia/Kolkata time):

```env
CLASSROOM_RESTRICTED_DAYS=<ISO weekdays, comma-separated; 1=Monday>
CLASSROOM_RESTRICTED_START=<HH:MM>
CLASSROOM_RESTRICTED_END=<HH:MM>
```

The API rejects any general-classroom request until all three values are set,
and blocks requests overlapping those hours. Also add real timetable events to
the calendar before claiming physical availability for a general classroom.

Calendar API references: [event metadata](https://developers.google.com/workspace/calendar/api/guides/extended-properties), [list events](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), [insert events](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert).
