"""
Streaming voice agent for Vapi integration.

Lifecycle log events emitted per turn:
  llm.start         — before each Anthropic streaming call
  voice.first_token — milliseconds from llm.start to first token (THE latency KPI)
  llm.end           — after the stream closes (duration, stop_reason, tool_calls)
  tool.start        — before each tool executes
  tool.end          — after each tool (duration, success)
  voice.turn.end    — end of the full turn (total_ms, chars_streamed, iterations)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncIterator, Optional

import anthropic

from agent.agent import PilatesAgent
from agent.providers.base import ToolCall
from agent.tools import ALL_TOOLS
from agent.voice_prompt import get_voice_system_prompt
from config.logging_config import Timer
from config.settings import Settings

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8
VOICE_MAX_TOKENS = 300


class VoiceAgent:
    def __init__(self, pilates_agent: PilatesAgent, settings: Settings) -> None:
        self._agent = pilates_agent
        self._settings = settings
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def stream_reply(
        self,
        call_id: str,
        vapi_messages: list[dict],
        customer_phone: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """
        Async generator — yields text tokens as they arrive from the LLM.

        Key latency metric: voice.first_token.latency_ms
        Everything else is context for debugging.
        """
        turn_start = time.perf_counter()

        session = self._agent._get_or_create_session(call_id)
        if customer_phone and not session.customer_phone:
            session.customer_phone = customer_phone
            logger.info("voice.caller_identified", extra={"phone": customer_phone})

        messages = [m for m in vapi_messages if m.get("role") != "system"]
        system = get_voice_system_prompt(self._settings.timezone)
        iterations = 0
        total_chars = 0

        while iterations < MAX_TOOL_ITERATIONS:
            iterations += 1
            tool_calls: list[ToolCall] = []
            iter_chars = 0
            first_token_logged = False
            llm_start = time.perf_counter()

            logger.info(
                "llm.start",
                extra={
                    "model": self._settings.anthropic_model,
                    "messages": len(messages),
                    "tools": len(ALL_TOOLS),
                    "iteration": iterations,
                },
            )

            try:
                async with self._client.messages.stream(
                    model=self._settings.anthropic_model,
                    max_tokens=VOICE_MAX_TOKENS,
                    system=system,
                    tools=ALL_TOOLS,
                    messages=self._strip_tool_name_field(messages),
                ) as stream:
                    async for text in stream.text_stream:
                        if not first_token_logged:
                            latency_ms = round((time.perf_counter() - llm_start) * 1000)
                            logger.info(
                                "voice.first_token",
                                extra={"latency_ms": latency_ms, "iteration": iterations},
                            )
                            first_token_logged = True
                        iter_chars += len(text)
                        total_chars += len(text)
                        yield text

                    final_message = await stream.get_final_message()

            except Exception as exc:
                logger.exception("voice.stream.error", extra={"error": str(exc), "iteration": iterations})
                yield "Sorry, I hit a technical issue. Let me have someone call you right back."
                break

            llm_duration = round((time.perf_counter() - llm_start) * 1000)

            for block in final_message.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

            logger.info(
                "llm.end",
                extra={
                    "duration_ms": llm_duration,
                    "stop_reason": "tool_use" if tool_calls else "end_turn",
                    "tool_calls": len(tool_calls),
                    "text_len": iter_chars,
                    "iteration": iterations,
                },
            )

            messages = messages + [{"role": "assistant", "content": final_message.content}]

            if not tool_calls:
                break

            # ── Execute tools ──────────────────────────────────────────
            tool_results: list[dict] = []
            for tc in tool_calls:
                logger.debug("tool.args", extra={"tool": tc.name, "args": tc.input})
                logger.info("tool.start", extra={"tool": tc.name, "iteration": iterations})

                with Timer() as tool_timer:
                    result = self._agent._dispatch_tool(session, tc.name, tc.input)

                success = "error" not in result
                logger.info(
                    "tool.end",
                    extra={
                        "tool": tc.name,
                        "duration_ms": tool_timer.elapsed_ms,
                        "success": success,
                        "iteration": iterations,
                    },
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "tool_name": tc.name,
                    "content": json.dumps(result, default=str),
                })

            messages = messages + [
                {"role": "user", "content": self._strip_tool_name_field_in_results(tool_results)}
            ]

        # ── Turn complete ──────────────────────────────────────────────
        total_ms = round((time.perf_counter() - turn_start) * 1000)
        logger.info(
            "voice.turn.end",
            extra={
                "total_ms": total_ms,
                "chars_streamed": total_chars,
                "iterations": iterations,
            },
        )

        session.messages = [m for m in messages if _is_text_turn(m)]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_tool_name_field(messages: list[dict]) -> list[dict]:
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
