"""Authorize the SmartAssist Google account for Calendar access locally.

Usage:
    python scripts/connect_calendar.py --client-secrets /path/to/oauth-desktop-client.json

The OAuth client JSON must be downloaded from the team's Google Cloud project.
The resulting refresh token is saved to the ignored repository-root .env file,
never printed to the terminal.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import set_key
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ACCOUNT_EMAIL = "smartassist.csis@gmail.com"
CALENDAR_SCOPE = ["https://www.googleapis.com/auth/calendar"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize the SmartAssist account for Calendar access.")
    parser.add_argument("--client-secrets", type=Path, required=True)
    args = parser.parse_args()

    if not args.client_secrets.is_file():
        parser.error("OAuth client JSON file not found.")

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secrets), CALENDAR_SCOPE)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not credentials.refresh_token:
        raise RuntimeError("Google did not issue an offline refresh token. Check the OAuth consent setup.")

    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    primary = service.calendarList().get(calendarId="primary").execute()
    calendar_id = primary.get("id", "")
    if calendar_id.casefold() != ACCOUNT_EMAIL:
        raise RuntimeError(
            f"Signed into {calendar_id or 'an unknown account'}, expected {ACCOUNT_EMAIL}. "
            "No configuration was saved. Retry and choose the SmartAssist account."
        )

    env_file = Path(__file__).resolve().parents[1] / ".env"
    env_file.touch(exist_ok=True)
    env_file.chmod(0o600)
    values = {
        "GOOGLE_CALENDAR_CLIENT_ID": credentials.client_id,
        "GOOGLE_CALENDAR_CLIENT_SECRET": credentials.client_secret,
        "GOOGLE_CALENDAR_REFRESH_TOKEN": credentials.refresh_token,
    }
    for key, value in values.items():
        set_key(str(env_file), key, value)
    env_file.chmod(0o600)
    print(f"Authorized {ACCOUNT_EMAIL}; Calendar credentials saved to ignored {env_file.name}.")
    print("Set GOOGLE_CALENDAR_ID=primary after confirming the room calendar is ready.")


if __name__ == "__main__":
    main()
