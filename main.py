"""Entry point for the Solstice Pilates AI receptionist."""

import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from agent.agent import PilatesAgent
from agent.voice_agent import VoiceAgent
from api.routes import router as chat_router, set_agent
from api.vapi_routes import router as vapi_router, set_voice_agent
from config.settings import get_settings

# Logging must be configured before any other import that uses a logger
_settings_for_log = get_settings()
from config.logging_config import setup_logging
setup_logging(level=_settings_for_log.log_level, fmt=_settings_for_log.log_format)

from integrations.google_calendar import GoogleCalendarClient
from integrations.google_sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    sa_file = settings.google_service_account_file
    if not Path(sa_file).exists():
        raise FileNotFoundError(
            f"Service account file not found: {sa_file}\n"
            "Place your Google service account JSON at that path."
        )

    # ── Google integrations ────────────────────────────────────────────
    calendar = GoogleCalendarClient(
        service_account_file=sa_file,
        calendar_id=settings.google_calendar_id,
        timezone=settings.timezone,
    )
    sheets = GoogleSheetsClient(
        service_account_file=sa_file,
        sheet_id=settings.google_sheet_id,
    )

    # ── Phase 1: text chat agent ───────────────────────────────────────
    pilates_agent = PilatesAgent(settings=settings, calendar=calendar, sheets=sheets)
    set_agent(pilates_agent)

    # ── Phase 2: voice agent (Vapi) ────────────────────────────────────
    # VoiceAgent wraps PilatesAgent — shares all booking/sheets logic.
    # Uses AsyncAnthropic for streaming so text tokens reach Vapi/TTS
    # before the full response is ready.
    if settings.anthropic_api_key:
        voice_agent = VoiceAgent(pilates_agent=pilates_agent, settings=settings)
        set_voice_agent(voice_agent)
        logger.info("Vapi voice agent ready on /vapi/chat and /vapi/webhook")
    else:
        logger.warning(
            "ANTHROPIC_API_KEY not set — Vapi voice routes will return 500. "
            "Voice always uses Anthropic streaming regardless of LLM_PROVIDER."
        )

    # ── FastAPI app ────────────────────────────────────────────────────
    app = FastAPI(
        title="Solstice Pilates AI Receptionist",
        description="Phase 1: text chat  |  Phase 2: Vapi voice",
        version="2.0.0",
    )

    app.include_router(chat_router)   # /chat, /session, /health
    app.include_router(vapi_router)   # /vapi/chat, /vapi/webhook

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = Path(__file__).parent / "ui" / "index.html"
        return HTMLResponse(content=html_path.read_text())

    logger.info("Solstice Pilates AI Receptionist ready. Provider: %s", settings.llm_provider)
    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
