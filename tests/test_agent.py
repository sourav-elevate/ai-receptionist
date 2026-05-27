"""
Unit tests for the Solstice Pilates agent.
All Google API and LLM calls are mocked — runs fully offline.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from agent.agent import PilatesAgent, Session
from agent.providers.base import LLMProvider, LLMResponse, ToolCall
from config.settings import Settings
from integrations.google_sheets import GoogleSheetsClient, _utcnow, _utcnow_plus


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_settings() -> Settings:
    return Settings(
        llm_provider="anthropic",
        anthropic_api_key="test-key",
        google_calendar_id="test@cal",
        google_sheet_id="test-sheet",
        google_service_account_file="none",
    )


def _make_agent(calendar_mock, sheets_mock, provider_mock=None):
    provider = provider_mock or MagicMock(spec=LLMProvider)
    return PilatesAgent(
        settings=_make_settings(),
        calendar=calendar_mock,
        sheets=sheets_mock,
        provider=provider,
    )


def _make_event(event_id="evt1", class_type="reformer", capacity=10):
    return {
        "id": event_id,
        "summary": "Reformer Pilates",
        "start": {"dateTime": "2026-05-29T18:00:00-07:00"},
        "end":   {"dateTime": "2026-05-29T18:55:00-07:00"},
        "extendedProperties": {
            "private": {
                "classType": class_type,
                "maxCapacity": str(capacity),
                "instructor": "Mia",
            }
        },
    }


# ---------------------------------------------------------------------------
# Provider base — canonical message formatting
# ---------------------------------------------------------------------------


class TestCanonicalFormats:
    def test_format_assistant_text_only(self):
        resp = LLMResponse(text="Hello!", tool_calls=[])
        msg = LLMProvider.format_assistant_message(resp)
        assert msg == {"role": "assistant", "content": "Hello!"}

    def test_format_assistant_with_tool_calls(self):
        tc = ToolCall(id="tc1", name="reserve_spot", input={"class_type": "reformer"})
        resp = LLMResponse(text="Let me hold that.", tool_calls=[tc])
        msg = LLMProvider.format_assistant_message(resp)
        assert isinstance(msg["content"], list)
        assert msg["content"][0] == {"type": "text", "text": "Let me hold that."}
        assert msg["content"][1]["type"] == "tool_use"
        assert msg["content"][1]["name"] == "reserve_spot"

    def test_format_tool_result_message(self):
        results = [{"tool_use_id": "tc1", "tool_name": "reserve_spot", "content": '{"success":true}'}]
        msg = LLMProvider.format_tool_result_message(results)
        assert msg["role"] == "user"
        assert msg["content"][0]["tool_name"] == "reserve_spot"


# ---------------------------------------------------------------------------
# Availability (still uses get_booking_count which now counts pending too)
# ---------------------------------------------------------------------------


class TestCheckAvailability:
    def test_available(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = _make_event(capacity=10)
        cal.get_max_capacity.return_value = 10
        sht.get_booking_count.return_value = 7   # 7 active+pending

        result = _make_agent(cal, sht)._check_availability("reformer", "2026-05-29", "18:00")
        assert result["available"] is True
        assert result["spots_remaining"] == 3

    def test_full(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = _make_event(capacity=10)
        cal.get_max_capacity.return_value = 10
        sht.get_booking_count.return_value = 10

        result = _make_agent(cal, sht)._check_availability("reformer", "2026-05-29", "18:00")
        assert result["available"] is False

    def test_not_found(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = None
        result = _make_agent(cal, sht)._check_availability("reformer", "2026-05-29", "18:00")
        assert result["reason"] == "no_class_found"


# ---------------------------------------------------------------------------
# Option B — reserve_spot (Phase 1)
# ---------------------------------------------------------------------------


class TestReserveSpot:
    def test_successful_reservation(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = _make_event(capacity=10)
        cal.get_max_capacity.return_value = 10
        sht.get_booking_count.return_value = 9    # 1 spot left
        sht.create_pending_booking.return_value = "RSVP-ABC1"

        session = Session("s1")
        result = _make_agent(cal, sht)._reserve_spot(session, "reformer", "2026-05-29", "18:00")

        assert result["success"] is True
        assert result["reservation_id"] == "RSVP-ABC1"
        assert result["expires_in_minutes"] == 10
        assert "spot_reserved" in session.actions_taken
        sht.create_pending_booking.assert_called_once()

    def test_fails_when_full(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = _make_event(capacity=10)
        cal.get_max_capacity.return_value = 10
        sht.get_booking_count.return_value = 10   # full (pending rows counted)

        session = Session("s1")
        result = _make_agent(cal, sht)._reserve_spot(session, "reformer", "2026-05-29", "18:00")

        assert result["success"] is False
        sht.create_pending_booking.assert_not_called()

    def test_fails_when_class_not_found(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = None

        result = _make_agent(cal, sht)._reserve_spot(Session("s1"), "reformer", "2026-05-29", "18:00")
        assert result["success"] is False
        sht.create_pending_booking.assert_not_called()


# ---------------------------------------------------------------------------
# Option B — confirm_booking (Phase 2)
# ---------------------------------------------------------------------------


class TestConfirmBooking:
    def test_successful_confirmation(self):
        cal, sht = MagicMock(), MagicMock()
        sht.confirm_booking.return_value = True

        session = Session("s1")
        result = _make_agent(cal, sht)._confirm_booking(
            session, "RSVP-ABC1", "Sara", "415-555-0190"
        )

        assert result["success"] is True
        assert result["booking_id"] == "RSVP-ABC1"
        assert session.customer_name == "Sara"
        assert session.customer_phone == "415-555-0190"
        assert "booked_class" in session.actions_taken
        sht.upsert_contact.assert_called_once_with(phone="415-555-0190", name="Sara")

    def test_fails_when_expired(self):
        cal, sht = MagicMock(), MagicMock()
        sht.confirm_booking.return_value = False   # sheets reports expired

        result = _make_agent(cal, sht)._confirm_booking(
            Session("s1"), "RSVP-OLD1", "Sara", "415-555-0190"
        )

        assert result["success"] is False
        assert "expired" in result["reason"].lower() or "not found" in result["reason"].lower()
        sht.upsert_contact.assert_not_called()


# ---------------------------------------------------------------------------
# Option A — threading.Lock prevents double-booking
# ---------------------------------------------------------------------------


class TestBookingLock:
    def test_lock_exists_per_event(self):
        agent = _make_agent(MagicMock(), MagicMock())
        lock_a = agent._booking_locks["evt1"]
        lock_b = agent._booking_locks["evt2"]
        assert lock_a is not lock_b
        assert agent._booking_locks["evt1"] is lock_a   # same object returned

    def test_second_caller_blocked_when_last_spot_taken(self):
        """
        Simulate two threads calling _reserve_spot for the same last spot.
        Only the first should succeed; the second should see it as full
        because the pending row written by the first is counted.
        """
        cal = MagicMock()
        cal.find_class_event.return_value = _make_event(event_id="evt_race", capacity=10)
        cal.get_max_capacity.return_value = 10

        call_count = {"n": 0}

        def booking_count_side_effect(event_id):
            # First call: 9 booked (1 spot free). Second call: 10 (pending row added).
            n = call_count["n"]
            call_count["n"] += 1
            return 9 if n == 0 else 10

        sht = MagicMock()
        sht.get_booking_count.side_effect = booking_count_side_effect
        sht.create_pending_booking.return_value = "RSVP-001"

        agent = _make_agent(cal, sht)
        results = []

        def reserve(session_id):
            s = Session(session_id)
            results.append(agent._reserve_spot(s, "reformer", "2026-05-29", "18:00"))

        t1 = threading.Thread(target=reserve, args=("s1",))
        t2 = threading.Thread(target=reserve, args=("s2",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        successes = [r for r in results if r.get("success")]
        failures  = [r for r in results if not r.get("success")]
        assert len(successes) == 1
        assert len(failures) == 1


# ---------------------------------------------------------------------------
# Sheets — get_booking_count respects pending + expiry
# ---------------------------------------------------------------------------


class TestGetBookingCount:
    """
    Unit-test the Sheets client logic in isolation using fake row data.
    No real API calls.
    """

    def _make_client(self):
        with patch("google.oauth2.service_account.Credentials.from_service_account_file"), \
             patch("googleapiclient.discovery.build"):
            return GoogleSheetsClient("fake.json", "fake_sheet_id")

    def _build_rows(self, bookings: list[dict]) -> list[list]:
        headers = GoogleSheetsClient.BOOKINGS_HEADERS
        rows = [headers]
        for b in bookings:
            row = [b.get(h, "") for h in headers]
            rows.append(row)
        return rows

    def test_counts_active_rows(self):
        client = self._make_client()
        rows = self._build_rows([
            {"booking_id": "B1", "event_id": "E1", "status": "active"},
            {"booking_id": "B2", "event_id": "E1", "status": "active"},
        ])
        client._get = MagicMock(return_value=rows)
        assert client.get_booking_count("E1") == 2

    def test_counts_non_expired_pending(self):
        client = self._make_client()
        rows = self._build_rows([
            {
                "booking_id": "B1", "event_id": "E1", "status": "pending",
                "expires_at": _utcnow_plus(5),   # still valid
            },
        ])
        client._get = MagicMock(return_value=rows)
        assert client.get_booking_count("E1") == 1

    def test_ignores_expired_pending(self):
        client = self._make_client()
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self._build_rows([
            {
                "booking_id": "B1", "event_id": "E1", "status": "pending",
                "expires_at": past,              # already expired
            },
        ])
        client._get = MagicMock(return_value=rows)
        assert client.get_booking_count("E1") == 0

    def test_active_plus_valid_pending(self):
        client = self._make_client()
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self._build_rows([
            {"booking_id": "B1", "event_id": "E1", "status": "active"},
            {"booking_id": "B2", "event_id": "E1", "status": "pending", "expires_at": _utcnow_plus(8)},
            {"booking_id": "B3", "event_id": "E1", "status": "pending", "expires_at": past},   # expired
            {"booking_id": "B4", "event_id": "E2", "status": "active"},   # different event
        ])
        client._get = MagicMock(return_value=rows)
        assert client.get_booking_count("E1") == 2   # B1 (active) + B2 (valid pending)


# ---------------------------------------------------------------------------
# Sheets — confirm_booking transitions pending → active
# ---------------------------------------------------------------------------


class TestConfirmBookingSheets:
    def _make_client(self):
        with patch("google.oauth2.service_account.Credentials.from_service_account_file"), \
             patch("googleapiclient.discovery.build"):
            return GoogleSheetsClient("fake.json", "fake_sheet_id")

    def _build_rows(self, bookings):
        headers = GoogleSheetsClient.BOOKINGS_HEADERS
        rows = [headers]
        for b in bookings:
            rows.append([b.get(h, "") for h in headers])
        return rows

    def test_confirms_valid_pending(self):
        client = self._make_client()
        rows = self._build_rows([{
            "booking_id": "RSVP-1", "event_id": "E1", "status": "pending",
            "expires_at": _utcnow_plus(8),
        }])
        client._get = MagicMock(return_value=rows)
        client._update = MagicMock()

        result = client.confirm_booking("RSVP-1", "Sara", "415-555-0190")

        assert result is True
        # Should update phone+name (C:D) and status (H)
        assert client._update.call_count == 2

    def test_rejects_expired_pending(self):
        client = self._make_client()
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self._build_rows([{
            "booking_id": "RSVP-OLD", "event_id": "E1", "status": "pending",
            "expires_at": past,
        }])
        client._get = MagicMock(return_value=rows)
        client._update = MagicMock()

        result = client.confirm_booking("RSVP-OLD", "Sara", "415-555-0190")

        assert result is False

    def test_rejects_already_active(self):
        client = self._make_client()
        rows = self._build_rows([{
            "booking_id": "BOOK-1", "event_id": "E1", "status": "active",
        }])
        client._get = MagicMock(return_value=rows)
        client._update = MagicMock()

        result = client.confirm_booking("BOOK-1", "Sara", "415-555-0190")

        assert result is False
        client._update.assert_not_called()


# ---------------------------------------------------------------------------
# Existing flows still work
# ---------------------------------------------------------------------------


class TestCancelBooking:
    def test_cancel_sets_cancelled(self):
        cal, sht = MagicMock(), MagicMock()
        sht.cancel_booking.return_value = True
        session = Session("s1")
        result = _make_agent(cal, sht)._cancel_booking(session, "BOOK-1")
        assert result["success"] is True
        assert "cancelled_booking" in session.actions_taken


class TestEscalation:
    def test_sets_session_flag(self):
        agent = _make_agent(MagicMock(), MagicMock())
        session = Session("s1")
        result = agent._escalate(session, "Billing dispute", "high", "415-555-0190")
        assert result["escalated"] is True
        assert session.escalated is True


class TestLogCall:
    def test_writes_to_sheets(self):
        cal, sht = MagicMock(), MagicMock()
        agent = _make_agent(cal, sht)
        session = Session("s1")
        session.actions_taken = ["spot_reserved", "booked_class"]
        result = agent._log_call(
            session, "415-555-0190", "Sara",
            "Booked Sara into the 6pm Reformer.",
            ["booked_class"], False,
        )
        assert result["logged"] is True
        sht.log_call.assert_called_once()


class TestStudioInfo:
    def test_returns_pricing(self):
        result = _make_agent(MagicMock(), MagicMock())._get_info("pricing")
        assert "$35" in str(result)

    def test_returns_all(self):
        result = _make_agent(MagicMock(), MagicMock())._get_info("all")
        assert "hours" in result and "pricing" in result


# ---------------------------------------------------------------------------
# OpenAI + Gemini translation helpers (unchanged, still valid)
# ---------------------------------------------------------------------------


class TestOpenAITranslation:
    def test_tool_schema_translation(self):
        from agent.providers.openai_provider import OpenAIProvider
        from agent.tools import ALL_TOOLS
        oai_tools = OpenAIProvider._to_openai_tools(ALL_TOOLS)
        for t in oai_tools:
            assert t["type"] == "function"
            assert "parameters" in t["function"]

    def test_tool_result_becomes_role_tool(self):
        from agent.providers.openai_provider import OpenAIProvider
        messages = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tc_1",
             "tool_name": "reserve_spot", "content": '{"success":true}'},
        ]}]
        oai = OpenAIProvider._to_openai_messages("sys", messages)
        tool_msg = next(m for m in oai if m.get("role") == "tool")
        assert tool_msg["tool_call_id"] == "tc_1"


class TestGeminiSchemaConversion:
    def test_converts_types(self):
        from agent.providers.gemini_provider import _convert_schema
        result = _convert_schema({"type": "object", "properties": {"x": {"type": "string"}}})
        assert result["type"] == "OBJECT"
        assert result["properties"]["x"]["type"] == "STRING"

    def test_tool_declarations(self):
        from agent.providers.gemini_provider import GeminiProvider
        from agent.tools import ALL_TOOLS
        from google.genai import types
        with patch("google.genai.Client"):
            p = GeminiProvider(api_key="test")
        tools = p._to_gemini_tools(ALL_TOOLS)
        assert isinstance(tools[0], types.Tool)
        assert len(tools[0].function_declarations) == len(ALL_TOOLS)


# ---------------------------------------------------------------------------
# Fix #1 — batch booking counts (N+1 fix)
# ---------------------------------------------------------------------------


class TestGetBookingCountsBatch:
    def _make_client(self):
        with patch("google.oauth2.service_account.Credentials.from_service_account_file"), \
             patch("googleapiclient.discovery.build"):
            return GoogleSheetsClient("fake.json", "fake_sheet_id")

    def _build_rows(self, bookings):
        headers = GoogleSheetsClient.BOOKINGS_HEADERS
        return [headers] + [[b.get(h, "") for h in headers] for b in bookings]

    def test_returns_correct_counts_for_multiple_events(self):
        client = self._make_client()
        rows = self._build_rows([
            {"booking_id": "B1", "event_id": "E1", "status": "active"},
            {"booking_id": "B2", "event_id": "E1", "status": "active"},
            {"booking_id": "B3", "event_id": "E2", "status": "active"},
            {"booking_id": "B4", "event_id": "E3", "status": "active"},
        ])
        client._get = MagicMock(return_value=rows)

        counts = client.get_booking_counts_batch(["E1", "E2", "E3"])

        assert counts == {"E1": 2, "E2": 1, "E3": 1}

    def test_reads_sheet_exactly_once(self):
        """The whole point of the fix — only one Sheets API call regardless of event count."""
        client = self._make_client()
        client._get = MagicMock(return_value=[GoogleSheetsClient.BOOKINGS_HEADERS])

        client.get_booking_counts_batch(["E1", "E2", "E3", "E4", "E5"])

        assert client._get.call_count == 1   # one call, not 5

    def test_excludes_expired_pending(self):
        client = self._make_client()
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self._build_rows([
            {"booking_id": "B1", "event_id": "E1", "status": "active"},
            {"booking_id": "B2", "event_id": "E1", "status": "pending", "expires_at": past},
            {"booking_id": "B3", "event_id": "E1", "status": "pending",
             "expires_at": _utcnow_plus(5)},
        ])
        client._get = MagicMock(return_value=rows)

        counts = client.get_booking_counts_batch(["E1"])
        assert counts["E1"] == 2   # active + valid pending, not expired

    def test_empty_event_ids_returns_empty_dict(self):
        client = self._make_client()
        client._get = MagicMock()

        result = client.get_booking_counts_batch([])

        assert result == {}
        client._get.assert_not_called()   # no API call at all

    def test_events_with_no_bookings_return_zero(self):
        client = self._make_client()
        client._get = MagicMock(return_value=[GoogleSheetsClient.BOOKINGS_HEADERS])

        counts = client.get_booking_counts_batch(["E_NEW_1", "E_NEW_2"])
        assert counts == {"E_NEW_1": 0, "E_NEW_2": 0}


class TestListClassesUsesOneBatchCall:
    """_list_classes must make exactly 1 Sheets call regardless of event count."""

    def test_single_sheets_call_for_many_events(self):
        cal = MagicMock()
        sht = MagicMock()

        # Simulate 10 upcoming events returned by Calendar
        events = [
            {
                "id": f"evt_{i}",
                "summary": f"Reformer Pilates",
                "start": {"dateTime": f"2026-06-0{i}T09:00:00-07:00"},
                "extendedProperties": {"private": {"classType": "reformer", "maxCapacity": "10"}},
            }
            for i in range(1, 11)
        ]
        cal.list_class_events.return_value = events
        cal.get_max_capacity.return_value = 10
        cal.get_class_type.return_value = "reformer"
        cal.get_instructor.return_value = "Mia"

        # Batch method returns all zeros (empty studio)
        sht.get_booking_counts_batch.return_value = {f"evt_{i}": 0 for i in range(1, 11)}

        agent = _make_agent(cal, sht)
        result = agent._list_classes(days_ahead=7)

        # Only ONE call to sheets, with all 10 event IDs
        sht.get_booking_counts_batch.assert_called_once()
        call_args = sht.get_booking_counts_batch.call_args[0][0]
        assert len(call_args) == 10

        # get_booking_count (single) must NOT have been called
        sht.get_booking_count.assert_not_called()

        assert result["total_found"] == 10

    def test_empty_calendar_returns_early_without_sheets_call(self):
        cal = MagicMock()
        sht = MagicMock()
        cal.list_class_events.return_value = []

        result = _make_agent(cal, sht)._list_classes()

        sht.get_booking_counts_batch.assert_not_called()
        assert result == {"classes": [], "total_found": 0}


# ---------------------------------------------------------------------------
# Fix #2 — async route uses to_thread (non-blocking event loop)
# ---------------------------------------------------------------------------


class TestAsyncRoute:
    """Verify the /chat endpoint doesn't block the event loop."""

    def test_chat_route_uses_asyncio_to_thread(self):
        """
        Two simultaneous requests each take 150 ms (simulated via time.sleep).
        With asyncio.to_thread they run in parallel threads — total wall time
        is ~150 ms.  Without it they would run serially — total ~300 ms.
        We assert that the second request STARTED before the first one ENDED,
        which can only happen if the event loop was free during the sleep.
        """
        import asyncio

        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        import api.routes as routes_module

        call_times = []

        def slow_chat(session_id, message):
            call_times.append(("start", time.time()))
            time.sleep(0.15)                    # blocks its worker thread, not the loop
            call_times.append(("end", time.time()))
            return "ok"

        # Use a plain MagicMock so we can set provider_name freely
        # (provider_name is a property on PilatesAgent, which has no setter)
        mock_agent = MagicMock()
        mock_agent.chat = slow_chat
        mock_agent.new_session.return_value = "test-session"
        mock_agent.provider_name = "test"

        test_app = FastAPI()
        original_agent = routes_module._agent
        routes_module.set_agent(mock_agent)
        test_app.include_router(routes_module.router)

        try:
            async def run():
                async with AsyncClient(
                    transport=ASGITransport(app=test_app), base_url="http://test"
                ) as client:
                    return await asyncio.gather(
                        client.post("/chat", json={"session_id": "s1", "message": "hi"}),
                        client.post("/chat", json={"session_id": "s2", "message": "hi"}),
                    )

            loop = asyncio.new_event_loop()
            responses = loop.run_until_complete(run())
            loop.close()

            assert all(r.status_code == 200 for r in responses)

            starts = sorted(t for label, t in call_times if label == "start")
            ends   = sorted(t for label, t in call_times if label == "end")

            # Second request must have started BEFORE the first one finished —
            # proving the two ran concurrently (event loop was not blocked).
            assert starts[1] < ends[0], (
                f"Requests ran serially (starts={starts}, ends={ends}). "
                "asyncio.to_thread is not working."
            )
        finally:
            routes_module.set_agent(original_agent)
