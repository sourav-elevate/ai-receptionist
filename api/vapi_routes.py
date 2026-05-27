"""
Vapi integration routes.

POST /vapi/chat    — Custom LLM endpoint (SSE streaming).
POST /vapi/webhook — End-of-call events.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent.voice_agent import VoiceAgent
from config.log_context import bind as bind_context
from config.logging_config import Timer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vapi")

_voice_agent: Optional[VoiceAgent] = None


def set_voice_agent(agent: VoiceAgent) -> None:
    global _voice_agent
    _voice_agent = agent


def get_voice_agent() -> VoiceAgent:
    if _voice_agent is None:
        raise RuntimeError("VoiceAgent not initialised")
    return _voice_agent


# ------------------------------------------------------------------
# Custom LLM endpoint
# ------------------------------------------------------------------

@router.post("/chat")
async def vapi_chat(request: Request) -> StreamingResponse:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    call_info = body.get("call", {})
    call_id = call_info.get("id") or uuid.uuid4().hex
    messages = body.get("messages", [])
    customer_phone: Optional[str] = (
        call_info.get("customer", {}).get("number")
        or call_info.get("phoneNumber", {}).get("number")
    )

    # Bind context so all log lines for this turn carry call_id and "voice"
    bind_context(call_id=call_id, session_id=call_id, channel="voice")

    logger.info(
        "vapi.turn.start",
        extra={"turns": len(messages), "phone": customer_phone or "-"},
    )

    agent = get_voice_agent()
    stream = agent.stream_reply(call_id, messages, customer_phone)

    return StreamingResponse(
        _to_openai_sse(stream, call_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _to_openai_sse(
    text_stream: AsyncIterator[str],
    call_id: str,
) -> AsyncIterator[str]:
    chunk_id = f"chatcmpl-{call_id[:8]}"

    with Timer() as t:
        async for text in text_stream:
            if not text:
                continue
            payload = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "model": "solstice-v1",
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}
                ],
            }
            yield f"data: {json.dumps(payload)}\n\n"

    stop_payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "model": "solstice-v1",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(stop_payload)}\n\n"
    yield "data: [DONE]\n\n"

    logger.info("vapi.turn.done", extra={"duration_ms": t.elapsed_ms})


# ------------------------------------------------------------------
# Webhook endpoint
# ------------------------------------------------------------------

@router.post("/webhook")
async def vapi_webhook(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "Invalid JSON"}

    message = body.get("message", {})
    event_type = message.get("type")

    logger.info("vapi.webhook", extra={"event_type": event_type or "-"})

    if event_type == "end-of-call-report":
        call = message.get("call", {})
        call_id = call.get("id")
        bind_context(call_id=call_id or "-", channel="voice")

        try:
            agent = get_voice_agent()
            session = agent._agent.get_session(call_id)
            if session and not session.logged:
                phone = session.customer_phone or "unknown"
                name = session.customer_name or "unknown"
                ended_reason = message.get("endedReason", "unknown")
                summary = f"Call ended ({ended_reason}). Actions: {', '.join(session.actions_taken) or 'none'}."
                agent._agent._sheets.log_call(
                    phone=phone,
                    name=name,
                    summary=summary,
                    actions=session.actions_taken,
                    escalated=session.escalated,
                    escalation_reason="call ended before agent logged",
                )
                session.logged = True
                logger.info("vapi.fallback_logged", extra={"phone": phone, "ended_reason": ended_reason})
        except Exception as exc:
            logger.exception("vapi.webhook.error", extra={"error": str(exc)})

    return {"ok": True}
