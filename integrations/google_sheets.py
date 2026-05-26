"""Google Sheets client for contacts, bookings, and call log tracking."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GoogleSheetsClient:
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    # Tab ranges
    CONTACTS_TAB = "Contacts"
    BOOKINGS_TAB = "Bookings"
    CALL_LOG_TAB = "CallLog"

    # Header rows — order matters; must match append/update logic
    CONTACTS_HEADERS = ["phone", "name", "email", "first_call_date", "last_call_date", "total_calls", "notes"]
    BOOKINGS_HEADERS = ["booking_id", "event_id", "customer_phone", "customer_name", "class_name", "class_datetime", "booked_at", "status", "notes"]
    CALL_LOG_HEADERS = ["timestamp", "phone", "name", "summary", "actions_taken", "escalated", "escalation_reason"]

    def __init__(self, service_account_file: str, sheet_id: str):
        self.sheet_id = sheet_id
        creds = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=self.SCOPES
        )
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self._sheets = svc.spreadsheets()

    # ------------------------------------------------------------------
    # Sheet initialisation (called once by setup script)
    # ------------------------------------------------------------------

    def ensure_tabs_exist(self) -> None:
        """Create tabs and header rows if they don't exist yet."""
        meta = self._sheets.get(spreadsheetId=self.sheet_id).execute()
        existing = {s["properties"]["title"] for s in meta["sheets"]}

        tabs_to_create = []
        for tab in [self.CONTACTS_TAB, self.BOOKINGS_TAB, self.CALL_LOG_TAB]:
            if tab not in existing:
                tabs_to_create.append({"addSheet": {"properties": {"title": tab}}})

        if tabs_to_create:
            self._sheets.batchUpdate(
                spreadsheetId=self.sheet_id, body={"requests": tabs_to_create}
            ).execute()

        # Write headers if rows are empty
        for tab, headers in [
            (self.CONTACTS_TAB, self.CONTACTS_HEADERS),
            (self.BOOKINGS_TAB, self.BOOKINGS_HEADERS),
            (self.CALL_LOG_TAB, self.CALL_LOG_HEADERS),
        ]:
            existing_values = self._get(f"{tab}!1:1")
            if not existing_values or not existing_values[0]:
                self._append(f"{tab}!A1", [headers])

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    def find_contact(self, phone: str) -> Optional[dict]:
        rows = self._get(f"{self.CONTACTS_TAB}!A:G")
        if len(rows) <= 1:
            return None
        headers = rows[0]
        for idx, row in enumerate(rows[1:], start=2):
            data = self._row_to_dict(headers, row)
            if data.get("phone") == phone:
                data["_row"] = idx
                return data
        return None

    def upsert_contact(self, phone: str, name: str = "", email: str = "") -> dict:
        """Find or create a contact, incrementing call count each time."""
        existing = self.find_contact(phone)
        now = _utcnow()

        if existing:
            row_num = existing["_row"]
            total_calls = int(existing.get("total_calls") or 0) + 1
            # Update name if we now know it and didn't before
            new_name = name if name else existing.get("name", "")
            self._update(f"{self.CONTACTS_TAB}!B{row_num}:F{row_num}", [[new_name, existing.get("email", ""), existing.get("first_call_date", now), now, str(total_calls)]])
            existing.update({"name": new_name, "last_call_date": now, "total_calls": str(total_calls)})
            return existing

        self._append(f"{self.CONTACTS_TAB}!A1", [[phone, name, email, now, now, "1", ""]])
        return {"phone": phone, "name": name, "email": email, "first_call_date": now, "last_call_date": now, "total_calls": "1", "notes": ""}

    # ------------------------------------------------------------------
    # Bookings
    # ------------------------------------------------------------------

    def create_booking(
        self,
        event_id: str,
        customer_phone: str,
        customer_name: str,
        class_name: str,
        class_datetime: str,
    ) -> str:
        booking_id = uuid.uuid4().hex[:8].upper()
        now = _utcnow()
        self._append(
            f"{self.BOOKINGS_TAB}!A1",
            [[booking_id, event_id, customer_phone, customer_name, class_name, class_datetime, now, "active", ""]],
        )
        return booking_id

    def get_booking_count(self, event_id: str) -> int:
        rows = self._get(f"{self.BOOKINGS_TAB}!A:I")
        if len(rows) <= 1:
            return 0
        headers = rows[0]
        count = 0
        for row in rows[1:]:
            data = self._row_to_dict(headers, row)
            if data.get("event_id") == event_id and data.get("status") == "active":
                count += 1
        return count

    def find_customer_bookings(self, phone: str) -> list[dict]:
        rows = self._get(f"{self.BOOKINGS_TAB}!A:I")
        if len(rows) <= 1:
            return []
        headers = rows[0]
        results = []
        for idx, row in enumerate(rows[1:], start=2):
            data = self._row_to_dict(headers, row)
            if data.get("customer_phone") == phone and data.get("status") == "active":
                data["_row"] = idx
                results.append(data)
        return results

    def cancel_booking(self, booking_id: str) -> bool:
        rows = self._get(f"{self.BOOKINGS_TAB}!A:I")
        if len(rows) <= 1:
            return False
        for idx, row in enumerate(rows[1:], start=2):
            if row and row[0] == booking_id:
                self._update(f"{self.BOOKINGS_TAB}!H{idx}", [["cancelled"]])
                return True
        return False

    def reschedule_booking(
        self, booking_id: str, new_event_id: str, new_class_name: str, new_class_datetime: str
    ) -> bool:
        rows = self._get(f"{self.BOOKINGS_TAB}!A:I")
        if len(rows) <= 1:
            return False
        headers = rows[0]
        for idx, row in enumerate(rows[1:], start=2):
            data = self._row_to_dict(headers, row)
            if data.get("booking_id") == booking_id:
                # Columns: B=event_id, E=class_name, F=class_datetime
                self._update(
                    f"{self.BOOKINGS_TAB}!B{idx}",
                    [[new_event_id]],
                )
                self._update(
                    f"{self.BOOKINGS_TAB}!E{idx}:F{idx}",
                    [[new_class_name, new_class_datetime]],
                )
                return True
        return False

    # ------------------------------------------------------------------
    # Call log
    # ------------------------------------------------------------------

    def log_call(
        self,
        phone: str,
        name: str,
        summary: str,
        actions: list[str],
        escalated: bool = False,
        escalation_reason: str = "",
    ) -> None:
        now = _utcnow()
        self._append(
            f"{self.CALL_LOG_TAB}!A1",
            [[now, phone, name, summary, ", ".join(actions), "yes" if escalated else "no", escalation_reason]],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, range_: str) -> list[list]:
        try:
            result = self._sheets.values().get(spreadsheetId=self.sheet_id, range=range_).execute()
            return result.get("values", [])
        except HttpError as e:
            raise RuntimeError(f"Sheets API error: {e}") from e

    def _append(self, range_: str, values: list[list]) -> None:
        try:
            self._sheets.values().append(
                spreadsheetId=self.sheet_id,
                range=range_,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            ).execute()
        except HttpError as e:
            raise RuntimeError(f"Sheets API error: {e}") from e

    def _update(self, range_: str, values: list[list]) -> None:
        try:
            self._sheets.values().update(
                spreadsheetId=self.sheet_id,
                range=range_,
                valueInputOption="RAW",
                body={"values": values},
            ).execute()
        except HttpError as e:
            raise RuntimeError(f"Sheets API error: {e}") from e

    @staticmethod
    def _row_to_dict(headers: list[str], row: list) -> dict:
        padded = row + [""] * (len(headers) - len(row))
        return dict(zip(headers, padded))
