"""Core AI receptionist agent for Solstice Pilates."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from agent.prompts import get_system_prompt
from agent.providers import LLMProvider, create_provider
from agent.tools import ALL_TOOLS
from config.settings import Settings
from config.studio_info import STUDIO_INFO
from integrations.google_calendar import GoogleCalendarClient
from integrations.google_sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """All state for one caller conversation."""

    session_id: str
    messages: list[dict] = field(default_factory=list)
    customer_phone: Optional[str] = None
    customer_name: Optional[str] = None
    actions_taken: list[str] = field(default_factory=list)
    escalated: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    logged: bool = False


class PilatesAgent:
    """AI receptionist backed by a pluggable LLM provider + Google integrations."""

    MAX_TOOL_ITERATIONS = 10

    def __init__(
        self,
        settings: Settings,
        calendar: GoogleCalendarClient,
        sheets: GoogleSheetsClient,
        provider: Optional[LLMProvider] = None,
    ) -> None:
        self._settings = settings
        self._calendar = calendar
        self._sheets = sheets
        self._provider: LLMProvider = provider or create_provider(settings)
        self._sessions: dict[str, Session] = {}

        # Option A — one threading.Lock per calendar event_id.
        # Serialises the check→write pair inside reserve_spot so two
        # simultaneous callers cannot both read "1 spot left" and both
        # succeed. The lock is held only for the duration of that pair
        # (milliseconds), not for the whole conversation.
        self._booking_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, session_id: str, user_message: str) -> str:
        session = self._get_or_create_session(session_id)
        session.messages.append({"role": "user", "content": user_message})
        return self._agent_loop(session)

    def new_session(self) -> str:
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = Session(session_id=session_id)
        return session_id

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    @property
    def provider_name(self) -> str:
        return type(self._provider).__name__.replace("Provider", "").lower()

    # ------------------------------------------------------------------
    # Agent loop
    # ------------------------------------------------------------------

    def _get_or_create_session(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id=session_id)
        return self._sessions[session_id]

    def _agent_loop(self, session: Session) -> str:
        system_prompt = get_system_prompt(self._settings.timezone)
        iterations = 0

        while iterations < self.MAX_TOOL_ITERATIONS:
            iterations += 1
            response = self._provider.complete(
                system=system_prompt,
                messages=session.messages,
                tools=ALL_TOOLS,
                max_tokens=self._settings.max_tokens,
            )
            session.messages.append(self._provider.format_assistant_message(response))

            if not response.has_tool_calls:
                return response.text or ""

            tool_results: list[dict] = []
            for tc in response.tool_calls:
                result = self._dispatch_tool(session, tc.name, tc.input)
                tool_results.append({
                    "tool_use_id": tc.id,
                    "tool_name": tc.name,
                    "content": json.dumps(result, default=str),
                })
            session.messages.append(self._provider.format_tool_result_message(tool_results))

        logger.error("Max tool iterations (%d) reached for session %s", self.MAX_TOOL_ITERATIONS, session.session_id)
        return "I'm sorry, I ran into an issue. Let me have someone from the team call you back."

    # ------------------------------------------------------------------
    # Tool dispatcher
    # ------------------------------------------------------------------

    def _dispatch_tool(self, session: Session, name: str, args: dict[str, Any]) -> dict:
        logger.info("Tool call: %s %s", name, args)
        try:
            match name:
                case "check_class_availability":
                    return self._check_availability(**args)
                case "list_available_classes":
                    return self._list_classes(**args)
                case "reserve_spot":
                    return self._reserve_spot(session, **args)
                case "confirm_booking":
                    return self._confirm_booking(session, **args)
                case "find_customer_bookings":
                    return self._find_bookings(session, **args)
                case "reschedule_booking":
                    return self._reschedule(session, **args)
                case "cancel_booking":
                    return self._cancel_booking(session, **args)
                case "get_studio_info":
                    return self._get_info(**args)
                case "log_call":
                    return self._log_call(session, **args)
                case "escalate_to_human":
                    return self._escalate(session, **args)
                case _:
                    return {"error": f"Unknown tool: {name}"}
        except Exception as exc:
            logger.exception("Tool %s failed: %s", name, exc)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _check_availability(self, class_type: str, date: str, time: str) -> dict:
        event = self._calendar.find_class_event(class_type, date, time)
        if not event:
            return {"available": False, "reason": "no_class_found", "spots_remaining": 0}

        max_cap = self._calendar.get_max_capacity(event)
        # get_booking_count counts active + non-expired pending rows
        booked = self._sheets.get_booking_count(event["id"])
        spots = max(0, max_cap - booked)

        return {
            "available": spots > 0,
            "spots_remaining": spots,
            "max_capacity": max_cap,
            "event_id": event["id"],
            "class_name": event.get("summary", ""),
            "start": event["start"].get("dateTime", ""),
        }

    def _list_classes(self, days_ahead: int = 7, class_type: str = "all") -> dict:
        events = self._calendar.list_class_events(
            days_ahead=days_ahead,
            class_type=class_type if class_type != "all" else None,
        )
        if not events:
            return {"classes": [], "total_found": 0}

        # Fix #1 — one Sheets call for all events instead of N calls.
        # get_booking_counts_batch reads the sheet once and returns a dict.
        booking_counts = self._sheets.get_booking_counts_batch(
            [e["id"] for e in events]
        )

        classes = []
        for event in events:
            max_cap = self._calendar.get_max_capacity(event)
            booked = booking_counts.get(event["id"], 0)   # plain dict lookup, no I/O
            spots = max(0, max_cap - booked)
            if spots > 0:
                classes.append({
                    "event_id": event["id"],
                    "class_name": event.get("summary", ""),
                    "class_type": self._calendar.get_class_type(event),
                    "start": event["start"].get("dateTime", ""),
                    "instructor": self._calendar.get_instructor(event),
                    "spots_remaining": spots,
                })
        return {"classes": classes, "total_found": len(classes)}

    def _reserve_spot(
        self,
        session: Session,
        class_type: str,
        date: str,
        time: str,
    ) -> dict:
        """
        Option A + B combined:
          - The threading.Lock (Option A) serialises the check+write for a given
            event so two simultaneous callers cannot both pass the capacity check.
          - Writing a PENDING row immediately (Option B) holds the spot for up to
            RESERVATION_TIMEOUT_MINUTES while the agent collects caller details.
            The lock is released as soon as the row is written — it is NOT held
            for the entire conversation.
        """
        event = self._calendar.find_class_event(class_type, date, time)
        if not event:
            return {"success": False, "reason": "Class not found on the schedule."}

        event_id = event["id"]

        # ── Option A: acquire per-event lock ──────────────────────────
        lock = self._booking_locks[event_id]
        with lock:
            max_cap = self._calendar.get_max_capacity(event)
            booked = self._sheets.get_booking_count(event_id)
            if booked >= max_cap:
                return {"success": False, "reason": "Sorry, that class just filled up."}

            # ── Option B: write pending row inside the lock ────────────
            # The spot is now counted as taken for any concurrent caller.
            reservation_id = self._sheets.create_pending_booking(
                event_id=event_id,
                class_name=event.get("summary", ""),
                class_datetime=event["start"].get("dateTime", ""),
            )
        # Lock released — conversation continues outside the critical section

        session.actions_taken.append("spot_reserved")
        logger.info("Reserved %s for event %s (session %s)", reservation_id, event_id, session.session_id)

        return {
            "success": True,
            "reservation_id": reservation_id,
            "class_name": event.get("summary", ""),
            "start": event["start"].get("dateTime", ""),
            "expires_in_minutes": 10,
            "message": "Spot is held. Collect name and phone, then call confirm_booking.",
        }

    def _confirm_booking(
        self,
        session: Session,
        reservation_id: str,
        customer_name: str,
        customer_phone: str,
    ) -> dict:
        """
        Option B Phase 2 — attach caller details and activate the reservation.
        Fails gracefully if the 10-minute window has passed.
        """
        ok = self._sheets.confirm_booking(
            booking_id=reservation_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
        )
        if not ok:
            return {
                "success": False,
                "reason": (
                    "Reservation not found or has expired. "
                    "Please check availability again and reserve a new spot."
                ),
            }

        if customer_phone:
            session.customer_phone = customer_phone
        if customer_name:
            session.customer_name = customer_name

        self._sheets.upsert_contact(phone=customer_phone, name=customer_name)
        session.actions_taken.append("booked_class")

        return {"success": True, "booking_id": reservation_id}

    def _find_bookings(self, session: Session, customer_phone: str) -> dict:
        if customer_phone:
            session.customer_phone = customer_phone
        bookings = self._sheets.find_customer_bookings(customer_phone)
        return {"bookings": bookings, "count": len(bookings)}

    def _reschedule(
        self,
        session: Session,
        booking_id: str,
        new_class_type: str,
        new_date: str,
        new_time: str,
    ) -> dict:
        new_event = self._calendar.find_class_event(new_class_type, new_date, new_time)
        if not new_event:
            return {"success": False, "reason": "New class not found on the schedule."}

        new_event_id = new_event["id"]
        lock = self._booking_locks[new_event_id]

        with lock:
            max_cap = self._calendar.get_max_capacity(new_event)
            booked = self._sheets.get_booking_count(new_event_id)
            if booked >= max_cap:
                return {"success": False, "reason": "New class is full."}
            ok = self._sheets.reschedule_booking(
                booking_id=booking_id,
                new_event_id=new_event_id,
                new_class_name=new_event.get("summary", ""),
                new_class_datetime=new_event["start"].get("dateTime", ""),
            )

        if ok:
            session.actions_taken.append("rescheduled_booking")
        return {
            "success": ok,
            "new_class_name": new_event.get("summary", ""),
            "new_start": new_event["start"].get("dateTime", ""),
        }

    def _cancel_booking(self, session: Session, booking_id: str) -> dict:
        ok = self._sheets.cancel_booking(booking_id)
        if ok:
            session.actions_taken.append("cancelled_booking")
        return {"success": ok}

    def _get_info(self, topic: str) -> dict:
        if topic == "all":
            return STUDIO_INFO
        return {topic: STUDIO_INFO.get(topic, "Information not available.")}

    def _log_call(
        self,
        session: Session,
        customer_phone: str,
        customer_name: str,
        summary: str,
        actions_taken: list[str],
        escalated: bool,
        escalation_reason: str = "",
    ) -> dict:
        all_actions = list(set(session.actions_taken + actions_taken))
        if customer_phone and customer_phone != "unknown":
            self._sheets.upsert_contact(
                phone=customer_phone,
                name=customer_name if customer_name != "unknown" else "",
            )
        self._sheets.log_call(
            phone=customer_phone,
            name=customer_name,
            summary=summary,
            actions=all_actions,
            escalated=escalated,
            escalation_reason=escalation_reason,
        )
        session.logged = True
        return {"logged": True}

    def _escalate(
        self,
        session: Session,
        reason: str,
        urgency: str,
        customer_phone: str,
        customer_name: str = "",
    ) -> dict:
        session.escalated = True
        session.actions_taken.append("escalated_to_human")
        if customer_phone:
            session.customer_phone = customer_phone
        if customer_name:
            session.customer_name = customer_name
        logger.warning(
            "ESCALATION [%s] phone=%s name=%s reason=%s",
            urgency.upper(), customer_phone, customer_name, reason,
        )
        return {
            "escalated": True,
            "urgency": urgency,
            "message": f"Flagged for human follow-up ({urgency} priority). Reason: {reason}",
        }
