"""Entry point for the Solstice Pilates AI receptionist."""

import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from agent.agent import PilatesAgent
from api.routes import router, set_agent
from config.settings import get_settings
from integrations.google_calendar import GoogleCalendarClient
from integrations.google_sheets import GoogleSheetsClient

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    settings = get_settings()

    sa_file = settings.google_service_account_file
    if not Path(sa_file).exists():
        raise FileNotFoundError(
            f"Service account file not found: {sa_file}\n"
            "Place your Google service account JSON at that path (see README)."
        )

    calendar = GoogleCalendarClient(
        service_account_file=sa_file,
        calendar_id=settings.google_calendar_id,
        timezone=settings.timezone,
    )
    sheets = GoogleSheetsClient(
        service_account_file=sa_file,
        sheet_id=settings.google_sheet_id,
    )
    agent = PilatesAgent(settings=settings, calendar=calendar, sheets=sheets)
    set_agent(agent)

    app = FastAPI(
        title="Solstice Pilates AI Receptionist",
        description="Phase 1 — text chat interface",
        version="1.0.0",
    )
    app.include_router(router)

    # Serve the chat UI at "/"
    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = Path(__file__).parent / "ui" / "index.html"
        return HTMLResponse(content=html_path.read_text())

    logger.info("Solstice Pilates AI Receptionist ready.")
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
