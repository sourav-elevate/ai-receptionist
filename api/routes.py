"""FastAPI routes for the Solstice Pilates AI receptionist."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent.agent import PilatesAgent

logger = logging.getLogger(__name__)

router = APIRouter()

# Agent instance is injected via dependency
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
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    provider: str  # which LLM backend answered


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
    """Create a new conversation session."""
    session_id = agent.new_session()
    return SessionResponse(session_id=session_id)


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, agent: PilatesAgent = Depends(get_agent)):
    """Send a message and get a reply. If session_id is omitted a new one is created."""
    session_id = body.session_id or agent.new_session()
    try:
        reply = agent.chat(session_id, body.message)
    except Exception as exc:
        logger.exception("Agent error: %s", exc)
        raise HTTPException(status_code=500, detail="Agent error. Please try again.")
    return ChatResponse(session_id=session_id, reply=reply, provider=agent.provider_name)


@router.get("/session/{session_id}/history")
async def get_history(session_id: str, agent: PilatesAgent = Depends(get_agent)):
    """Return the conversation history for a session (debug endpoint)."""
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # Return only text turns for readability
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
