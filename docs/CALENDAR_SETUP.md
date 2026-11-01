# Room calendar setup

The enabled rooms are the **CSIS Conference Room** and **D-153**. D-153 is a
general classroom; it is bookable at any time until college hours are set with
`CLASSROOM_RESTRICTED_*` (below). The older room
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
2. Share the calendar with the project's service account (the one in
   `GOOGLE_SERVICE_ACCOUNT_JSON_B64`, currently
   `drive-and-calendar-bot@csis-smartassist-502216.iam.gserviceaccount.com`).
   Signed in as `smartassist.csis@gmail.com`, open Google Calendar settings,
   select the calendar under **Settings for my calendars**, and under **Share
   with specific people or groups** add the service account with **Make changes
   to events**. The Google Calendar API must be enabled in the same Cloud project.
3. Set `GOOGLE_CALENDAR_ID=smartassist.csis@gmail.com` in `.env`. Do not use
   `primary` here: with a service account, `primary` is the service account's
   own calendar. Leave `GOOGLE_CALENDAR_SUBJECT` and the `GOOGLE_CALENDAR_CLIENT_*`
   / refresh token values unset; a Calendar refresh token takes precedence over
   the service account if present. A service account cannot invite attendees, so
   events are created without them.
4. Restart the API. Copy `GOOGLE_CALENDAR_ID` (and the service account JSON) to
   the API host's secret configuration for deployment; the local `.env` is not
   deployed.
5. Verify a request and admin approval with a real conference-room slot. The
   approval should create an event on the account calendar, and the booking
   should store its Calendar event ID and link. Recheck a conflicting slot.

### Alternative: account OAuth

To act as the account itself (for example, to send attendee invitations),
create a **Desktop app** OAuth client in the Cloud project with consent
configured for `smartassist.csis@gmail.com` (see Google's
[Python quickstart](https://developers.google.com/workspace/calendar/api/quickstart/python)),
keep the JSON outside the repository, and run:

```sh
.venv/bin/python scripts/connect_calendar.py --client-secrets /absolute/path/to/oauth-client.json
```

Sign in as `smartassist.csis@gmail.com`. The helper saves the OAuth client ID,
secret, and Calendar refresh token in the ignored root `.env` without printing
the token; then set `GOOGLE_CALENDAR_ID=primary`. The Gmail sending token cannot
be assumed to have Calendar permission. If the OAuth consent project is
**External / Testing**, Google says its offline refresh token expires after
seven days; see Google's
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

To restrict a general classroom such as D-153 to outside college hours, set
these server-side values (Asia/Kolkata time):

```env
CLASSROOM_RESTRICTED_DAYS=<ISO weekdays, comma-separated; 1=Monday>
CLASSROOM_RESTRICTED_START=<HH:MM>
CLASSROOM_RESTRICTED_END=<HH:MM>
```

With all three unset, general classrooms have no time restriction. With all
three set, the API blocks requests overlapping those hours; a partial
configuration rejects every general-classroom request. Also add real timetable
events to the calendar before claiming physical availability for a general
classroom.

Calendar API references: [event metadata](https://developers.google.com/workspace/calendar/api/guides/extended-properties), [list events](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), [insert events](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert).
