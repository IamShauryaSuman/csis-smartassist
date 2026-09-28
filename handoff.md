# Calendar implementation handoff

## Repository and state

- Repository: https://github.com/IamShauryaSuman/csis-smartassist
- Working directory: `/Users/abhinav/Documents/csis_smart_assist/csis-smartassist`
- Branch: `abhinav/calendar`, pushed to `origin` (commit `654c708` plus the 2026-09-23 review follow-up). Not merged to `main`.
- `plan.md` is intentionally ignored by `.gitignore` and contains the earlier task plan. This `handoff.md` is **not** ignored and should be included if the work is committed.
- No Supabase migration, Google Calendar authorization, calendar event, deployment, or other live change has been made from this checkout.

## User decisions and scope

The user asked for the calendar portion of CSIS SmartAssist to be implemented. They supplied the Google account `smartassist.csis@gmail.com` and confirmed:

- One shared calendar for room bookings.
- The account calendar currently has no room schedule; it can be used to build a booking schedule. Do **not** invent an academic timetable or claim that an empty calendar proves physical availability.
- 2026-09-28: user confirmed D-153 stays bookable alongside the **CSIS Conference Room**. Migration 007 now enables both. D-153 is a general classroom; user wants it bookable at any time for now, so `CLASSROOM_RESTRICTED_*` stays unset (unset now means no restriction; partial config still fails closed). User confirmed DLT-8 stays unbookable.
- Defer recurring bookings. The semester-end date is irrelevant in this phase.
- Academic/study-use and availability acknowledgements are wanted.
- Calendar access works through the shared service account (see Remaining work 2).

## Implemented locally

- `supabase/migrations/007_priority_classrooms.sql` adds `booking_enabled` and `is_general_classroom` to rooms, stages D-153 disabled, enables the CSIS Conference Room, and adds booking acknowledgements and Calendar event fields. Older illustrative room seeds remain disabled by default.
- `api/core/rooms.py` returns only enabled rooms for proposals and uses a shared `GOOGLE_CALENDAR_ID` when a room-specific calendar ID is absent.
- `api/services/calendar.py` supports account OAuth or service-account access. It separates events on a shared calendar by private room metadata or unambiguous title/location, treats unknown events/errors as unknown availability, and creates events with deterministic IDs for retry recovery.
- `api/routes/booking.py` requires acknowledgements, checks the slot on request and approval, refuses disabled rooms, and records approval only after Calendar event creation. The diagnostic Calendar endpoint now requires admin access.
- `api/core/booking_policy.py` implements configurable college-hours restrictions for general classrooms, but those rooms are disabled now.
- Chat prompt and booking UI describe an empty calendar as **no recorded booking**, with actual room availability subject to admin approval. UI displays event links and Asia/Kolkata times.
- `.env.example`, `docs/SETUP.md`, and `docs/CALENDAR_SETUP.md` document configuration. `scripts/connect_calendar.py` is an interactive Desktop OAuth helper that checks the signed-in primary calendar belongs to `smartassist.csis@gmail.com` and stores credentials in ignored `.env` with restrictive permissions. It does not activate `GOOGLE_CALENDAR_ID` automatically.
- `api/requirements.txt` adds `google-auth-oauthlib`.

## Verification already run

- Backend: `cd api && ../.venv/bin/pytest -q --ignore=tests/test_db.py` — **57 passed**. `test_db.py` is excluded because it constructs a Supabase client at import time and no Supabase URL is configured here.
- UI: `cd ui && npm test` — **68 passed**.
- UI: `cd ui && npm run build` — passed.
- UI: `cd ui && npx tsc --noEmit` — passed after the build. An earlier TypeScript run raced the build while `.next/types` was being regenerated; it was not a code failure.
- `git diff --check` — passed.
- `.venv/bin/python scripts/connect_calendar.py --help` — passed. Actual OAuth has **not** been run.

## Code review (2026-09-23)

Reviewed the migration, room enablement, booking approval, calendar service, and OAuth helper. No blocking issues. Findings:

- Fixed: the draft booking card showed times parsed from offset-stripped strings, so they were wrong in any browser outside IST. It now formats `payload.start_time`/`end_time` directly; regression test added in `ui/__tests__/booking-proposal.test.tsx`.
- Deploy order: `get_all_rooms()` filters on `rooms.booking_enabled`, which only exists after migration 007. **Apply migration 007 before deploying this API**, or chat and room listing will error.
- With only the conference room enabled on `GOOGLE_CALENDAR_ID=primary`, availability uses FreeBusy, so every event on that account's primary calendar counts as busy for the room. This is conservative (never reports a false free slot); keep unrelated personal events off that calendar.
- Existing pending bookings for the illustrative rooms from migration 004 can be rejected but not approved once 007 disables those rooms. Intended.
- Approved bookings still cannot be cancelled/rejected afterwards (pre-existing), so there is no path that deletes an approved Calendar event. Out of scope for this phase.
- Re-verified locally: backend 57 passed, UI 69 passed, `tsc --noEmit` and `npm run build` passed.

## Remaining work

1. If college hours are later required for D-153, set all three `CLASSROOM_RESTRICTED_*` values.
2. Done 2026-09-28: Calendar access uses the existing service account `drive-and-calendar-bot@csis-smartassist-502216.iam.gserviceaccount.com` instead of Desktop OAuth. The `smartassist.csis@gmail.com` calendar is shared with it ("Make changes to events"). Local `.env` has `GOOGLE_CALENDAR_ID=smartassist.csis@gmail.com` (not `primary`, which would mean the service account's own calendar). Verified read, FreeBusy, event insert and delete. The service account cannot invite attendees; the code already skips attendees in this mode. The deployed API also needs `GOOGLE_CALENDAR_ID` set.
3. (Superseded by 2.)
4. Done 2026-09-28: the user applied migration 007 in the Supabase SQL editor. Verified via REST: only `csis_conference_room` and `d_153` have `booking_enabled = true` (DLT-8 and the 004 rooms are disabled); booking acknowledgement and calendar event columns exist. No pending bookings existed at the time.
5. Done 2026-09-28: live end-to-end check by calling the route handlers in-process (auth and emails stubbed): conference-room request → pending; admin approval → event on `smartassist.csis@gmail.com` with room/booking private metadata and a stored link; overlapping conference-room request → 409; D-153 at the same slot → pending (shared calendar keeps rooms separate). All test rows and the event were deleted afterwards. Not yet exercised through the deployed UI.
6. Merge to `main` and deploy only as directed by the user. Set `GOOGLE_CALENDAR_ID=smartassist.csis@gmail.com` on the API host first. Until the new API is deployed, the currently deployed API does not read `booking_enabled` and may still offer the disabled rooms.

## Important implementation cautions

- The older migration `004_rooms_table.sql` contains illustrative rooms and calendar IDs. Migration 007 leaves them disabled; do not re-enable without confirming real data.
- The Calendar refresh token must include Calendar scope; the Gmail sending token cannot be assumed to have it. If an External OAuth app remains in Testing, Google's offline refresh token expires after seven days: https://support.google.com/cloud/answer/15549945?hl=en.
- Calendar access, the migration, and a local end-to-end check are done. Remaining: deploy with `GOOGLE_CALENDAR_ID` set, then retest through the live UI.
