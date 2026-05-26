"""
One-time setup script: populates Google Calendar with test class events
and initialises the Google Sheet with the required tabs + headers.

Run from the project root:
    python -m scripts.setup_fixtures
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Make sure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings
from integrations.google_calendar import GoogleCalendarClient
from integrations.google_sheets import GoogleSheetsClient


# ---------------------------------------------------------------------------
# Class schedule definition
# ---------------------------------------------------------------------------

CLASS_SCHEDULE: list[dict] = [
    # Reformer classes – Mon / Wed / Fri
    {"class_type": "reformer", "name": "Reformer Pilates",    "hour": 9,  "minute": 0,  "duration": 55, "capacity": 10, "instructor": "Mia",     "days": [0, 2, 4]},
    {"class_type": "reformer", "name": "Reformer Pilates",    "hour": 11, "minute": 0,  "duration": 55, "capacity": 10, "instructor": "Jordan",  "days": [0, 2, 4]},
    {"class_type": "reformer", "name": "Reformer Pilates",    "hour": 18, "minute": 0,  "duration": 55, "capacity": 10, "instructor": "Mia",     "days": [0, 2, 4]},
    {"class_type": "reformer", "name": "Reformer Pilates",    "hour": 19, "minute": 0,  "duration": 55, "capacity": 10, "instructor": "Jordan",  "days": [0, 2, 4]},
    # Mat classes – Tue / Thu / Sat
    {"class_type": "mat",      "name": "Mat Pilates",         "hour": 8,  "minute": 30, "duration": 50, "capacity": 15, "instructor": "Sam",     "days": [1, 3, 5]},
    {"class_type": "mat",      "name": "Mat Pilates",         "hour": 12, "minute": 0,  "duration": 50, "capacity": 15, "instructor": "Sam",     "days": [1, 3, 5]},
    {"class_type": "mat",      "name": "Mat Pilates",         "hour": 17, "minute": 30, "duration": 50, "capacity": 15, "instructor": "Alex",    "days": [1, 3]},
    # Tower classes – Tue / Fri
    {"class_type": "tower",    "name": "Tower Pilates",       "hour": 10, "minute": 0,  "duration": 55, "capacity": 8,  "instructor": "Alex",    "days": [1, 4]},
    {"class_type": "tower",    "name": "Tower Pilates",       "hour": 16, "minute": 0,  "duration": 55, "capacity": 8,  "instructor": "Jordan",  "days": [1, 4]},
]

DAYS_TO_CREATE = 14  # two weeks ahead


def main() -> None:
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)

    print("Connecting to Google Calendar…")
    calendar = GoogleCalendarClient(
        service_account_file=settings.google_service_account_file,
        calendar_id=settings.google_calendar_id,
        timezone=settings.timezone,
    )

    print("Connecting to Google Sheets…")
    sheets = GoogleSheetsClient(
        service_account_file=settings.google_service_account_file,
        sheet_id=settings.google_sheet_id,
    )

    # ----------------------------------------------------------------
    # Sheets: ensure tabs + headers exist
    # ----------------------------------------------------------------
    print("Initialising Google Sheet tabs…")
    sheets.ensure_tabs_exist()
    print("  Tabs OK.")

    # ----------------------------------------------------------------
    # Calendar: create class events
    # ----------------------------------------------------------------
    today = datetime.now(tz).date()
    events_created = 0

    for cls in CLASS_SCHEDULE:
        for day_offset in range(DAYS_TO_CREATE):
            target_date = today + timedelta(days=day_offset)
            if target_date.weekday() not in cls["days"]:
                continue

            start = datetime(
                target_date.year,
                target_date.month,
                target_date.day,
                cls["hour"],
                cls["minute"],
                tzinfo=tz,
            )

            # Skip events in the past
            if start < datetime.now(tz):
                continue

            # Check if an identical event already exists to avoid duplicates
            existing = calendar.find_class_event(
                class_type=cls["class_type"],
                date=target_date.strftime("%Y-%m-%d"),
                time=f"{cls['hour']:02d}:{cls['minute']:02d}",
            )
            if existing:
                print(f"  SKIP  {cls['name']} {start.strftime('%a %b %d %I:%M %p')} (already exists)")
                continue

            event = calendar.create_class_event(
                summary=cls["name"],
                start=start,
                duration_minutes=cls["duration"],
                class_type=cls["class_type"],
                max_capacity=cls["capacity"],
                instructor=cls["instructor"],
            )
            events_created += 1
            print(f"  CREATED  {cls['name']} ({cls['class_type']})  {start.strftime('%a %b %d %I:%M %p')}  cap={cls['capacity']}  id={event['id'][:12]}…")

    print(f"\nDone. Created {events_created} calendar events.")
    print("Setup complete — you can now run the agent.")


if __name__ == "__main__":
    main()
