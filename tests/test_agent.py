"""
Unit tests for the Solstice Pilates agent.

All Google API calls and LLM provider calls are mocked — tests run offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.agent import PilatesAgent, Session
from agent.providers.base import LLMProvider, LLMResponse, ToolCall
from config.settings import Settings


# ---------------------------------------------------------------------------
# Helpers
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


def _make_calendar_event(event_id="evt1", class_type="reformer", capacity=10):
    return {
        "id": event_id,
        "summary": "Reformer Pilates",
        "start": {"dateTime": "2025-06-05T18:00:00-07:00"},
        "end": {"dateTime": "2025-06-05T18:55:00-07:00"},
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
        tc = ToolCall(id="tc1", name="check_class_availability", input={"class_type": "reformer"})
        resp = LLMResponse(text="Let me check.", tool_calls=[tc])
        msg = LLMProvider.format_assistant_message(resp)
        assert msg["role"] == "assistant"
        assert isinstance(msg["content"], list)
        assert msg["content"][0] == {"type": "text", "text": "Let me check."}
        assert msg["content"][1]["type"] == "tool_use"
        assert msg["content"][1]["name"] == "check_class_availability"

    def test_format_tool_result_message(self):
        results = [
            {"tool_use_id": "tc1", "tool_name": "check_class_availability", "content": '{"available": true}'},
        ]
        msg = LLMProvider.format_tool_result_message(results)
        assert msg["role"] == "user"
        assert msg["content"][0]["type"] == "tool_result"
        assert msg["content"][0]["tool_use_id"] == "tc1"
        assert msg["content"][0]["tool_name"] == "check_class_availability"


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------


class TestCheckAvailability:
    def test_spots_available(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = _make_calendar_event(capacity=10)
        cal.get_max_capacity.return_value = 10
        sht.get_booking_count.return_value = 3

        agent = _make_agent(cal, sht)
        result = agent._check_availability("reformer", "2025-06-05", "18:00")

        assert result["available"] is True
        assert result["spots_remaining"] == 7

    def test_class_full(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = _make_calendar_event(capacity=10)
        cal.get_max_capacity.return_value = 10
        sht.get_booking_count.return_value = 10

        agent = _make_agent(cal, sht)
        result = agent._check_availability("reformer", "2025-06-05", "18:00")

        assert result["available"] is False
        assert result["spots_remaining"] == 0

    def test_class_not_found(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = None

        agent = _make_agent(cal, sht)
        result = agent._check_availability("reformer", "2025-06-05", "18:00")

        assert result["available"] is False
        assert result["reason"] == "no_class_found"


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


class TestBookClass:
    def test_successful_booking(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = _make_calendar_event(capacity=10)
        cal.get_max_capacity.return_value = 10
        sht.get_booking_count.return_value = 5
        sht.create_booking.return_value = "BOOK1234"

        agent = _make_agent(cal, sht)
        session = Session(session_id="s1")
        result = agent._book_class(
            session, "reformer", "2025-06-05", "18:00", "Sara", "415-555-0190"
        )

        assert result["success"] is True
        assert result["booking_id"] == "BOOK1234"
        assert "booked_class" in session.actions_taken
        assert session.customer_phone == "415-555-0190"

    def test_booking_blocked_when_full(self):
        cal, sht = MagicMock(), MagicMock()
        cal.find_class_event.return_value = _make_calendar_event(capacity=10)
        cal.get_max_capacity.return_value = 10
        sht.get_booking_count.return_value = 10

        agent = _make_agent(cal, sht)
        result = agent._book_class(
            Session("s1"), "reformer", "2025-06-05", "18:00", "Sara", "415-555-0190"
        )

        assert result["success"] is False
        sht.create_booking.assert_not_called()


# ---------------------------------------------------------------------------
# Cancel / Reschedule
# ---------------------------------------------------------------------------


class TestCancelBooking:
    def test_successful_cancel(self):
        cal, sht = MagicMock(), MagicMock()
        sht.cancel_booking.return_value = True

        agent = _make_agent(cal, sht)
        session = Session(session_id="s1")
        result = agent._cancel_booking(session, "BOOK1234")

        assert result["success"] is True
        assert "cancelled_booking" in session.actions_taken


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


class TestEscalation:
    def test_escalate_sets_session_flag(self):
        agent = _make_agent(MagicMock(), MagicMock())
        session = Session(session_id="s1")

        result = agent._escalate(session, "Billing dispute", "high", "415-555-0190")

        assert result["escalated"] is True
        assert session.escalated is True
        assert "escalated_to_human" in session.actions_taken


# ---------------------------------------------------------------------------
# Call logging
# ---------------------------------------------------------------------------


class TestLogCall:
    def test_log_call_writes_to_sheets(self):
        cal, sht = MagicMock(), MagicMock()
        agent = _make_agent(cal, sht)
        session = Session(session_id="s1")
        session.actions_taken = ["booked_class"]

        result = agent._log_call(
            session,
            customer_phone="415-555-0190",
            customer_name="Sara",
            summary="Booked Sara into the 6pm Reformer.",
            actions_taken=["booked_class"],
            escalated=False,
        )

        assert result["logged"] is True
        assert session.logged is True
        sht.log_call.assert_called_once()
        sht.upsert_contact.assert_called_once_with(phone="415-555-0190", name="Sara")


# ---------------------------------------------------------------------------
# Studio info
# ---------------------------------------------------------------------------


class TestGetStudioInfo:
    def test_returns_pricing_section(self):
        agent = _make_agent(MagicMock(), MagicMock())
        result = agent._get_info("pricing")
        assert "pricing" in result
        assert "$35" in str(result)

    def test_returns_all_sections(self):
        agent = _make_agent(MagicMock(), MagicMock())
        result = agent._get_info("all")
        assert "hours" in result
        assert "pricing" in result
        assert "cancellation_policy" in result


# ---------------------------------------------------------------------------
# Provider name
# ---------------------------------------------------------------------------


class TestProviderName:
    def test_provider_name_lowercased(self):
        from agent.providers.anthropic_provider import AnthropicProvider

        with patch("anthropic.Anthropic"):
            provider = AnthropicProvider(api_key="test")
        agent = _make_agent(MagicMock(), MagicMock(), provider_mock=provider)
        assert agent.provider_name == "anthropic"


# ---------------------------------------------------------------------------
# OpenAI translation helpers
# ---------------------------------------------------------------------------


class TestOpenAITranslation:
    def test_tool_schema_translation(self):
        from agent.providers.openai_provider import OpenAIProvider
        from agent.tools import ALL_TOOLS

        oai_tools = OpenAIProvider._to_openai_tools(ALL_TOOLS)
        for t in oai_tools:
            assert t["type"] == "function"
            assert "name" in t["function"]
            assert "parameters" in t["function"]

    def test_text_message_passthrough(self):
        from agent.providers.openai_provider import OpenAIProvider

        messages = [{"role": "user", "content": "Hello"}]
        oai = OpenAIProvider._to_openai_messages("You are a receptionist.", messages)
        assert oai[0]["role"] == "system"
        assert oai[1] == {"role": "user", "content": "Hello"}

    def test_tool_result_becomes_role_tool(self):
        from agent.providers.openai_provider import OpenAIProvider

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tc_1",
                        "tool_name": "get_studio_info",
                        "content": '{"pricing": "$35"}',
                    }
                ],
            }
        ]
        oai = OpenAIProvider._to_openai_messages("sys", messages)
        tool_msg = next(m for m in oai if m.get("role") == "tool")
        assert tool_msg["tool_call_id"] == "tc_1"
        assert tool_msg["content"] == '{"pricing": "$35"}'


# ---------------------------------------------------------------------------
# Gemini schema conversion
# ---------------------------------------------------------------------------


class TestGeminiSchemaConversion:
    def test_converts_types_to_uppercase(self):
        from agent.providers.gemini_provider import _convert_schema

        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["name"],
        }
        result = _convert_schema(schema)
        assert result["type"] == "OBJECT"
        assert result["properties"]["name"]["type"] == "STRING"
        assert result["properties"]["count"]["type"] == "INTEGER"

    def test_tool_declarations_format(self):
        from agent.providers.gemini_provider import GeminiProvider
        from agent.tools import ALL_TOOLS
        from google.genai import types

        with patch("google.genai.Client"):
            provider = GeminiProvider(api_key="test")

        gemini_tools = provider._to_gemini_tools(ALL_TOOLS)
        assert len(gemini_tools) == 1
        assert isinstance(gemini_tools[0], types.Tool)
        assert len(gemini_tools[0].function_declarations) == len(ALL_TOOLS)
