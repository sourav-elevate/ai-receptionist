"""Google Calendar client for Solstice Pilates class schedule management."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class GoogleCalendarClient:
    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, service_account_file: str, calendar_id: str, timezone: str = "America/Los_Angeles"):
        self.calendar_id = calendar_id
        self.tz = ZoneInfo(timezone)
        creds = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=self.SCOPES
        )
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_class_events(self, days_ahead: int = 7, class_type: Optional[str] = None) -> list[dict]:
        """Return upcoming class events, optionally filtered by class_type."""
        now = datetime.now(self.tz)
        time_max = now + timedelta(days=days_ahead)

        try:
            result = (
                self._service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=now.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Calendar API error: {e}") from e

        events = result.get("items", [])
        if class_type and class_type != "all":
            events = [e for e in events if self._get_prop(e, "classType") == class_type]
        return events

    def find_class_event(self, class_type: str, date: str, time: str) -> Optional[dict]:
        """Find a specific class event by type, date (YYYY-MM-DD), and time (HH:MM 24h)."""
        target_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M").replace(tzinfo=self.tz)
        window_start = target_dt - timedelta(minutes=5)
        window_end = target_dt + timedelta(minutes=5)

        try:
            result = (
                self._service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=window_start.isoformat(),
                    timeMax=window_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except HttpError as e:
            raise RuntimeError(f"Calendar API error: {e}") from e

        for event in result.get("items", []):
            if self._get_prop(event, "classType") == class_type:
                return event
        return None

    def get_event(self, event_id: str) -> dict:
        try:
            return self._service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as e:
            raise RuntimeError(f"Calendar API error: {e}") from e

    # ------------------------------------------------------------------
    # Writing (fixture setup only — bookings are tracked in Sheets)
    # ------------------------------------------------------------------

    def create_class_event(
        self,
        summary: str,
        start: datetime,
        duration_minutes: int,
        class_type: str,
        max_capacity: int,
        instructor: str = "TBD",
    ) -> dict:
        """Create a class event on the studio calendar (used by setup script)."""
        end = start + timedelta(minutes=duration_minutes)
        body = {
            "summary": summary,
            "description": f"Instructor: {instructor}",
            "start": {"dateTime": start.isoformat(), "timeZone": str(self.tz)},
            "end": {"dateTime": end.isoformat(), "timeZone": str(self.tz)},
            "extendedProperties": {
                "private": {
                    "classType": class_type,
                    "maxCapacity": str(max_capacity),
                    "instructor": instructor,
                }
            },
        }
        try:
            return self._service.events().insert(calendarId=self.calendar_id, body=body).execute()
        except HttpError as e:
            raise RuntimeError(f"Calendar API error: {e}") from e

    def delete_event(self, event_id: str) -> None:
        try:
            self._service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as e:
            raise RuntimeError(f"Calendar API error: {e}") from e

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_max_capacity(self, event: dict) -> int:
        return int(self._get_prop(event, "maxCapacity") or "10")

    def get_class_type(self, event: dict) -> str:
        return self._get_prop(event, "classType") or "unknown"

    def get_instructor(self, event: dict) -> str:
        return self._get_prop(event, "instructor") or "Staff"

    def format_event_summary(self, event: dict) -> str:
        start_str = event["start"].get("dateTime", "")
        if start_str:
            dt = datetime.fromisoformat(start_str)
            dt_local = dt.astimezone(self.tz)
            time_str = dt_local.strftime("%A %b %d at %-I:%M %p")
        else:
            time_str = event["start"].get("date", "")
        return f"{event.get('summary', 'Class')} — {time_str}"

    @staticmethod
    def _get_prop(event: dict, key: str) -> Optional[str]:
        return event.get("extendedProperties", {}).get("private", {}).get(key)
