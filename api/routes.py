"""FastAPI routes for the Solstice Pilates AI receptionist (text chat)."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent.agent import PilatesAgent
from config.log_context import bind as bind_context
from config.logging_config import Timer

logger = logging.getLogger(__name__)

router = APIRouter()

_agent: Optional[PilatesAgent] = None


def set_agent(agent: PilatesAgent) -> None:
    global _agent
    _agent = agent


def get_agent() -> PilatesAgent:
    if _agent is None:
        raise RuntimeError("Agent not initialised")
    return _agent


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(..., max_length=2000)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    provider: str


class SessionResponse(BaseModel):
    session_id: str


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/health")
async def health(agent: PilatesAgent = Depends(get_agent)):
    return {
        "status": "ok",
        "service": "Solstice Pilates AI Receptionist",
        "provider": agent.provider_name,
    }


@router.post("/session", response_model=SessionResponse)
async def create_session(agent: PilatesAgent = Depends(get_agent)):
    session_id = agent.new_session()
    return SessionResponse(session_id=session_id)


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, agent: PilatesAgent = Depends(get_agent)):
    session_id = body.session_id or agent.new_session()

    # Bind context so every log line down the stack carries session_id + channel
    bind_context(call_id=session_id, session_id=session_id, channel="text")

    logger.info(
        "request.start",
        extra={"endpoint": "/chat", "msg_len": len(body.message), "session_id": session_id},
    )

    with Timer() as t:
        try:
            reply = await asyncio.to_thread(agent.chat, session_id, body.message)
        except Exception as exc:
            logger.exception("request.error", extra={"error": str(exc)})
            raise HTTPException(status_code=500, detail="Agent error. Please try again.")

    logger.info(
        "request.done",
        extra={"duration_ms": t.elapsed_ms, "reply_len": len(reply), "status": 200},
    )

    return ChatResponse(session_id=session_id, reply=reply, provider=agent.provider_name)


@router.get("/session/{session_id}/history")
async def get_history(session_id: str, agent: PilatesAgent = Depends(get_agent)):
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    turns = []
    for msg in session.messages:
        content = msg["content"]
        if isinstance(content, str):
            turns.append({"role": msg["role"], "text": content})
        elif isinstance(content, list):
            text_parts = [b.get("text") or b.get("content") or "" for b in content if isinstance(b, dict)]
            combined = " ".join(filter(None, text_parts))
            if combined:
                turns.append({"role": msg["role"], "text": combined})
    return {"session_id": session_id, "turns": turns}
