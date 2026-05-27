"""
Vapi integration routes.

POST /vapi/chat    — Custom LLM endpoint.  Vapi sends every caller turn
                     here in OpenAI chat-completion format and expects a
                     streaming SSE response.

POST /vapi/webhook — Event handler.  Vapi posts end-of-call-report here
                     so we can finalise the call log in Google Sheets even
                     when the caller hangs up before the agent calls log_call.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent.voice_agent import VoiceAgent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vapi")

# Injected from main.py at startup
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
    """
    Vapi calls this for every caller turn.

    Request body (OpenAI chat completion format):
        {
          "model": "...",
          "messages": [{"role": "user", "content": "..."}, ...],
          "stream": true,
          "call": {"id": "...", "customer": {"number": "+1415..."}},
          "metadata": {}
        }

    Response: SSE stream of OpenAI chat.completion.chunk objects.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    call_info = body.get("call", {})
    call_id = call_info.get("id") or uuid.uuid4().hex
    messages = body.get("messages", [])

    # Extract caller's phone number from Vapi call metadata
    customer_phone: Optional[str] = (
        call_info.get("customer", {}).get("number")
        or call_info.get("phoneNumber", {}).get("number")
    )

    logger.info("Vapi call %s  phone=%s  turns=%d", call_id, customer_phone, len(messages))

    agent = get_voice_agent()
    stream = agent.stream_reply(call_id, messages, customer_phone)

    return StreamingResponse(
        _to_openai_sse(stream, call_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


async def _to_openai_sse(
    text_stream: AsyncIterator[str],
    call_id: str,
) -> AsyncIterator[str]:
    """
    Wrap text token chunks in OpenAI SSE chat.completion.chunk format.
    Vapi reads these chunks and feeds them to TTS as they arrive.
    """
    chunk_id = f"chatcmpl-{call_id[:8]}"

    async for text in text_stream:
        if not text:
            continue
        payload = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "model": "solstice-v1",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": text},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(payload)}\n\n"

    # Final stop chunk
    stop_payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "model": "solstice-v1",
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop"}
        ],
    }
    yield f"data: {json.dumps(stop_payload)}\n\n"
    yield "data: [DONE]\n\n"


# ------------------------------------------------------------------
# Webhook endpoint
# ------------------------------------------------------------------

@router.post("/webhook")
async def vapi_webhook(request: Request) -> dict:
    """
    Receives Vapi server messages (end-of-call-report, etc).

    Vapi sends an end-of-call-report when the call ends regardless of
    how it ended (caller hangup, max duration, agent goodbye).
    We use this to ensure log_call is always written — even if the
    caller hung up before the agent finished.
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "Invalid JSON"}

    message = body.get("message", {})
    event_type = message.get("type")

    logger.info("Vapi webhook: %s", event_type)

    if event_type == "end-of-call-report":
        call = message.get("call", {})
        call_id = call.get("id")

        try:
            agent = get_voice_agent()
            session = agent._agent.get_session(call_id)
            if session and not session.logged:
                # Agent didn't get to call log_call — do it now
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
                    escalation_reason="call ended before agent logged" if not session.logged else "",
                )
                session.logged = True
                logger.info("Fallback log_call written for call %s", call_id)
        except Exception as exc:
            logger.exception("Webhook log_call failed for call %s: %s", call_id, exc)

    return {"ok": True}
