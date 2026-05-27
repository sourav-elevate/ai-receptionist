from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # ── Google integrations ───────────────────────────────────────────
    google_calendar_id: str
    google_sheet_id: str
    google_service_account_file: str = "credentials/service_account.json"
    timezone: str = "America/Los_Angeles"

    # ── LLM provider (Phase 1 text chat) ──────────────────────────────
    llm_provider: str = "anthropic"   # anthropic | openai | gemini

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Google Gemini
    google_ai_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # Agent tuning (shared between text + voice)
    max_tokens: int = 1024

    # ── Vapi voice integration (Phase 2) ──────────────────────────────
    # vapi_api_key    : get from app.vapi.ai → Account → API Keys
    # server_base_url : public URL where Vapi can reach this server
    #                   (e.g. ngrok URL in dev, your domain in prod)
    # elevenlabs_voice_id: ElevenLabs voice for TTS
    # vapi_assistant_id  : filled in after running create_vapi_assistant.py
    vapi_api_key: str = ""
    server_base_url: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"   # Rachel
    vapi_assistant_id: str = ""

    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8")

    @model_validator(mode="after")
    def check_provider_key(self) -> "Settings":
        required = {
            "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
            "openai":    ("openai_api_key",    "OPENAI_API_KEY"),
            "gemini":    ("google_ai_api_key", "GOOGLE_AI_API_KEY"),
        }
        provider = self.llm_provider.lower()
        if provider in required:
            attr, env_var = required[provider]
            if not getattr(self, attr):
                raise ValueError(
                    f"{env_var} is required when LLM_PROVIDER={provider}"
                )
        return self


@lru_cache()
def get_settings() -> Settings:
    return Settings()
