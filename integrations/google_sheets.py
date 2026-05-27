"""Google Sheets client — contacts, bookings (two-phase), and call log."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# How long a pending reservation holds a spot before it auto-expires.
RESERVATION_TIMEOUT_MINUTES = 10


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utcnow_plus(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class GoogleSheetsClient:
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    CONTACTS_TAB = "Contacts"
    BOOKINGS_TAB = "Bookings"
    CALL_LOG_TAB = "CallLog"

    CONTACTS_HEADERS = [
        "phone", "name", "email",
        "first_call_date", "last_call_date", "total_calls", "notes",
    ]

    # Two new columns (reserved_at, expires_at) support the two-phase reservation.
    # Column map:
    #   A=booking_id  B=event_id  C=customer_phone  D=customer_name
    #   E=class_name  F=class_datetime  G=booked_at  H=status
    #   I=reserved_at  J=expires_at  K=notes
    BOOKINGS_HEADERS = [
        "booking_id", "event_id", "customer_phone", "customer_name",
        "class_name", "class_datetime", "booked_at", "status",
        "reserved_at", "expires_at", "notes",
    ]

    CALL_LOG_HEADERS = [
        "timestamp", "phone", "name", "summary",
        "actions_taken", "escalated", "escalation_reason",
    ]

    def __init__(self, service_account_file: str, sheet_id: str):
        self.sheet_id = sheet_id
        creds = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=self.SCOPES
        )
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self._sheets = svc.spreadsheets()

    # ------------------------------------------------------------------
    # Initialisation + schema migration
    # ------------------------------------------------------------------

    def ensure_tabs_exist(self) -> None:
        """Create tabs if missing, write headers, and migrate old schemas."""
        meta = self._sheets.get(spreadsheetId=self.sheet_id).execute()
        existing_tabs = {s["properties"]["title"] for s in meta["sheets"]}

        new_tabs = [
            tab for tab in [self.CONTACTS_TAB, self.BOOKINGS_TAB, self.CALL_LOG_TAB]
            if tab not in existing_tabs
        ]
        if new_tabs:
            self._sheets.batchUpdate(
                spreadsheetId=self.sheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": t}}} for t in new_tabs]},
            ).execute()

        for tab, headers in [
            (self.CONTACTS_TAB,  self.CONTACTS_HEADERS),
            (self.BOOKINGS_TAB,  self.BOOKINGS_HEADERS),
            (self.CALL_LOG_TAB,  self.CALL_LOG_HEADERS),
        ]:
            existing_row = self._get(f"{tab}!1:1")
            if not existing_row or not existing_row[0]:
                self._append(f"{tab}!A1", [headers])
            elif existing_row[0] != headers:
                # Schema changed (e.g. reserved_at/expires_at were added).
                # Overwrite header row; existing data rows get empty cells for
                # new columns, which is safe — all readers pad with "".
                self._update(f"{tab}!A1", [headers])
                logger.info("Migrated %s headers: %s → %s columns", tab, len(existing_row[0]), len(headers))

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
        existing = self.find_contact(phone)
        now = _utcnow()

        if existing:
            row_num = existing["_row"]
            total_calls = int(existing.get("total_calls") or 0) + 1
            new_name = name if name else existing.get("name", "")
            self._update(
                f"{self.CONTACTS_TAB}!B{row_num}:F{row_num}",
                [[new_name, existing.get("email", ""), existing.get("first_call_date", now), now, str(total_calls)]],
            )
            existing.update({"name": new_name, "last_call_date": now, "total_calls": str(total_calls)})
            return existing

        self._append(f"{self.CONTACTS_TAB}!A1", [[phone, name, email, now, now, "1", ""]])
        return {"phone": phone, "name": name, "email": email,
                "first_call_date": now, "last_call_date": now, "total_calls": "1", "notes": ""}

    # ------------------------------------------------------------------
    # Bookings — two-phase (Option B)
    # ------------------------------------------------------------------

    def create_pending_booking(
        self,
        event_id: str,
        class_name: str,
        class_datetime: str,
        timeout_minutes: int = RESERVATION_TIMEOUT_MINUTES,
    ) -> str:
        """
        Phase 1 — write a PENDING row that holds the spot immediately.
        The row counts toward capacity until it expires or is confirmed.
        Returns the booking_id (used as reservation_id by the caller).
        """
        booking_id = uuid.uuid4().hex[:8].upper()
        now = _utcnow()
        expires = _utcnow_plus(timeout_minutes)
        self._append(
            f"{self.BOOKINGS_TAB}!A1",
            [[booking_id, event_id, "", "", class_name, class_datetime,
              now, "pending", now, expires, ""]],
        )
        logger.info("Pending booking %s created for event %s (expires %s)", booking_id, event_id, expires)
        return booking_id

    def confirm_booking(
        self,
        booking_id: str,
        customer_name: str,
        customer_phone: str,
    ) -> bool:
        """
        Phase 2 — attach the caller's details and flip status to ACTIVE.
        Returns False if the reservation is not found, already used, or expired.
        """
        rows = self._get(f"{self.BOOKINGS_TAB}!A:K")
        if len(rows) <= 1:
            return False
        headers = rows[0]
        now = _utcnow()

        for idx, row in enumerate(rows[1:], start=2):
            data = self._row_to_dict(headers, row)
            if data.get("booking_id") != booking_id:
                continue
            if data.get("status") != "pending":
                logger.warning("confirm_booking: %s status is %s, not pending", booking_id, data.get("status"))
                return False
            expires_at = data.get("expires_at", "")
            if expires_at and expires_at < now:
                logger.warning("confirm_booking: %s expired at %s", booking_id, expires_at)
                self._update(f"{self.BOOKINGS_TAB}!H{idx}", [["expired"]])
                return False

            # Write customer details (C, D) and flip status (H) to active
            self._update(f"{self.BOOKINGS_TAB}!C{idx}:D{idx}", [[customer_phone, customer_name]])
            self._update(f"{self.BOOKINGS_TAB}!H{idx}", [["active"]])
            logger.info("Booking %s confirmed for %s (%s)", booking_id, customer_name, customer_phone)
            return True

        return False

    def get_booking_count(self, event_id: str) -> int:
        """Single-event wrapper around the batch method. Used by reserve_spot."""
        return self.get_booking_counts_batch([event_id]).get(event_id, 0)

    def get_booking_counts_batch(self, event_ids: list[str]) -> dict[str, int]:
        """
        Read the Bookings sheet ONCE and return {event_id: count} for every
        requested event.  Counts active rows + non-expired pending rows.

        Replaces N separate get_booking_count calls in _list_classes, cutting
        the Sheets API calls from N (one per event) down to 1.
        """
        if not event_ids:
            return {}

        rows = self._get(f"{self.BOOKINGS_TAB}!A:K")
        counts: dict[str, int] = {eid: 0 for eid in event_ids}

        if len(rows) <= 1:
            return counts

        headers = rows[0]
        now = _utcnow()
        id_set = set(event_ids)

        for row in rows[1:]:
            data = self._row_to_dict(headers, row)
            eid = data.get("event_id", "")
            if eid not in id_set:
                continue
            status = data.get("status")
            if status == "active":
                counts[eid] += 1
            elif status == "pending":
                expires_at = data.get("expires_at", "")
                if expires_at and expires_at > now:
                    counts[eid] += 1

        return counts

    def find_customer_bookings(self, phone: str) -> list[dict]:
        """Return active + non-expired pending bookings for a phone number."""
        rows = self._get(f"{self.BOOKINGS_TAB}!A:K")
        if len(rows) <= 1:
            return []
        headers = rows[0]
        now = _utcnow()
        results = []

        for idx, row in enumerate(rows[1:], start=2):
            data = self._row_to_dict(headers, row)
            if data.get("customer_phone") != phone:
                continue
            status = data.get("status")
            if status == "active":
                data["_row"] = idx
                results.append(data)
            elif status == "pending":
                expires_at = data.get("expires_at", "")
                if expires_at and expires_at > now:
                    data["_row"] = idx
                    results.append(data)

        return results

    def cancel_booking(self, booking_id: str) -> bool:
        rows = self._get(f"{self.BOOKINGS_TAB}!A:K")
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
        rows = self._get(f"{self.BOOKINGS_TAB}!A:K")
        if len(rows) <= 1:
            return False
        headers = rows[0]
        for idx, row in enumerate(rows[1:], start=2):
            data = self._row_to_dict(headers, row)
            if data.get("booking_id") == booking_id and data.get("status") == "active":
                self._update(f"{self.BOOKINGS_TAB}!B{idx}", [[new_event_id]])
                self._update(f"{self.BOOKINGS_TAB}!E{idx}:F{idx}", [[new_class_name, new_class_datetime]])
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
        self._append(
            f"{self.CALL_LOG_TAB}!A1",
            [[_utcnow(), phone, name, summary,
              ", ".join(actions), "yes" if escalated else "no", escalation_reason]],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, range_: str) -> list[list]:
        try:
            result = self._sheets.values().get(
                spreadsheetId=self.sheet_id, range=range_
            ).execute()
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
