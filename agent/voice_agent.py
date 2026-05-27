"""
Streaming voice agent for Vapi integration.

Uses Anthropic's async streaming API so the first spoken word reaches
the caller before the full response is ready — critical for < 1.2s
perceived latency on a live call.

Streaming flow for a booking query:
  1. Vapi sends: "Can I book the 6pm Reformer Thursday?"
  2. LLM immediately yields: "One sec, let me check."
                              → Vapi TTS starts playing (~200ms in)
  3. [tool: check_class_availability executes silently]
  4. LLM yields: "That one's full — the 7pm has space, want that?"
                              → Vapi speaks the answer
  Total perceived latency from end of caller speech: ~600–900ms
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator, Optional

import anthropic

from agent.agent import PilatesAgent
from agent.providers.base import ToolCall
from agent.tools import ALL_TOOLS
from agent.voice_prompt import get_voice_system_prompt
from config.settings import Settings

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8
VOICE_MAX_TOKENS = 300   # voice answers are short; cap to keep latency tight


class VoiceAgent:
    """
    Wraps PilatesAgent for voice.  Uses an AsyncAnthropic client so
    text tokens stream to Vapi as they arrive, instead of waiting for
    the full response.

    Tool execution reuses PilatesAgent._dispatch_tool() so all business
    logic (booking, sheets, calendar) is shared with the text chat path.
    """

    def __init__(self, pilates_agent: PilatesAgent, settings: Settings) -> None:
        self._agent = pilates_agent
        self._settings = settings
        # Async Anthropic client — separate from the sync one in AnthropicProvider
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def stream_reply(
        self,
        call_id: str,
        vapi_messages: list[dict],
        customer_phone: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """
        Async generator — yields text tokens as they arrive from the LLM.
        Vapi consumes these as SSE chunks and immediately feeds them to TTS.

        call_id     : Vapi's call.id — used as session key for booking state
        vapi_messages: full conversation history from Vapi (OpenAI format)
        customer_phone: caller's number from Vapi call metadata (if available)
        """
        session = self._agent._get_or_create_session(call_id)
        if customer_phone and not session.customer_phone:
            session.customer_phone = customer_phone
            logger.info("Voice call %s from %s", call_id, customer_phone)

        # Vapi sends OpenAI-format messages; strip the system turn (we own it)
        messages = [m for m in vapi_messages if m.get("role") != "system"]
        system = get_voice_system_prompt(self._settings.timezone)
        iterations = 0

        while iterations < MAX_TOOL_ITERATIONS:
            iterations += 1
            tool_calls: list[ToolCall] = []

            # ── Stream one LLM turn ────────────────────────────────────
            async with self._client.messages.stream(
                model=self._settings.anthropic_model,
                max_tokens=VOICE_MAX_TOKENS,
                system=system,
                tools=ALL_TOOLS,
                messages=self._strip_tool_name_field(messages),
            ) as stream:
                # Yield text tokens the moment they arrive
                async for text in stream.text_stream:
                    yield text

                final_message = await stream.get_final_message()

            # Append the full assistant turn to our working messages
            messages = messages + [
                {"role": "assistant", "content": final_message.content}
            ]

            # Collect any tool calls from the response
            for block in final_message.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append(
                        ToolCall(id=block.id, name=block.name, input=block.input)
                    )

            if not tool_calls:
                break   # no more tools — final text was already streamed

            # ── Execute tools (sync, fast) ─────────────────────────────
            tool_results: list[dict] = []
            for tc in tool_calls:
                result = self._agent._dispatch_tool(session, tc.name, tc.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "tool_name": tc.name,   # used by Gemini; stripped for Anthropic
                    "content": json.dumps(result, default=str),
                })
                logger.info("Voice tool %s → %s", tc.name, result)

            messages = messages + [
                {"role": "user", "content": self._strip_tool_name_field_in_results(tool_results)}
            ]

        # ── Update session with plain text turns only ──────────────────
        # Session.messages tracks the conversation for potential re-use.
        # We store only the text turns Vapi already knows about.
        session.messages = [m for m in messages if _is_text_turn(m)]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_tool_name_field(messages: list[dict]) -> list[dict]:
        """Remove the `tool_name` key from tool_result blocks before sending to Anthropic."""
        result = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                cleaned = [
                    {k: v for k, v in b.items() if k != "tool_name"}
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                    else b
                    for b in content
                ]
                result.append({**msg, "content": cleaned})
            else:
                result.append(msg)
        return result

    @staticmethod
    def _strip_tool_name_field_in_results(results: list[dict]) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "tool_name"} for r in results]


def _is_text_turn(msg: dict) -> bool:
    """True for plain string turns (not tool-use / tool-result block turns)."""
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return all(
            isinstance(b, dict) and b.get("type") == "text"
            for b in content
            if isinstance(b, dict)
        )
    return False
