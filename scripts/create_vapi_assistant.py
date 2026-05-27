"""
Create or update the Solstice Pilates assistant on Vapi.

Usage:
    python -m scripts.create_vapi_assistant            # create new
    python -m scripts.create_vapi_assistant --update   # update existing (reads VAPI_ASSISTANT_ID from .env)

After running, copy the printed assistant ID into your .env file:
    VAPI_ASSISTANT_ID=<id>

Then in Vapi dashboard, assign a phone number to the assistant.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from config.settings import get_settings
from config.vapi_config import get_assistant_config

VAPI_BASE_URL = "https://api.vapi.ai"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="Update existing assistant")
    args = parser.parse_args()

    settings = get_settings()

    if not settings.vapi_api_key:
        print("ERROR: VAPI_API_KEY is not set in .env")
        sys.exit(1)
    if not settings.server_base_url:
        print("ERROR: SERVER_BASE_URL is not set in .env  (e.g. https://abc123.ngrok.io)")
        sys.exit(1)

    config = get_assistant_config(
        server_base_url=settings.server_base_url.rstrip("/"),
        elevenlabs_voice_id=settings.elevenlabs_voice_id,
    )

    headers = {
        "Authorization": f"Bearer {settings.vapi_api_key}",
        "Content-Type": "application/json",
    }

    if args.update:
        if not settings.vapi_assistant_id:
            print("ERROR: VAPI_ASSISTANT_ID is not set. Run without --update first.")
            sys.exit(1)
        url = f"{VAPI_BASE_URL}/assistant/{settings.vapi_assistant_id}"
        resp = httpx.patch(url, headers=headers, json=config, timeout=30)
        action = "Updated"
    else:
        url = f"{VAPI_BASE_URL}/assistant"
        resp = httpx.post(url, headers=headers, json=config, timeout=30)
        action = "Created"

    if resp.status_code not in (200, 201):
        print(f"ERROR {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()
    assistant_id = data.get("id")

    print(f"\n{action} Vapi assistant successfully!")
    print(f"  Name:  {data.get('name')}")
    print(f"  ID:    {assistant_id}")
    print(f"  LLM:   {config['model']['url']}")
    print(f"  Voice: {config['voice']['voiceId']} ({config['voice']['provider']})")
    print(f"\nAdd to your .env:")
    print(f"  VAPI_ASSISTANT_ID={assistant_id}")
    print(f"\nNext: assign a phone number in the Vapi dashboard → Phone Numbers → Assign to assistant.")


if __name__ == "__main__":
    main()
