# Solstice Pilates — AI Receptionist

AI receptionist for a boutique pilates studio. Handles bookings, reschedules, pricing questions, drop-in enquiries, and escalates billing complaints to staff. Backed by Claude + Google Calendar + Google Sheets.

## Quick Start

### 1. Credentials

**Google:**
1. Create a Google Cloud project and enable the **Calendar API** and **Sheets API**.
2. Create a **Service Account**, download the JSON key, save it to `credentials/service_account.json`.
3. Share your Google Calendar with the service account email (give it **Make changes to events** permission).
4. Share your Google Sheet with the service account email (give it **Editor** permission).

**Anthropic:** Get an API key at https://console.anthropic.com.

### 2. Environment

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, GOOGLE_CALENDAR_ID, GOOGLE_SHEET_ID
```

### 3. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Seed test data

Populates the Calendar with 2 weeks of classes and initialises the Sheet tabs:

```bash
python -m scripts.setup_fixtures
```

### 5. Run

```bash
python main.py
# or: uvicorn main:app --reload
```

Open **http://localhost:8000** — the chat UI appears.

### 6. Test

```bash
pytest tests/ -v
```

---

## API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/session` | Create a new conversation session |
| POST | `/chat` | Send a message, get a reply |
| GET | `/session/{id}/history` | Debug: full turn history |
| GET | `/health` | Healthcheck |

**`POST /chat` body:**
```json
{ "session_id": "optional-existing-id", "message": "Is the 6pm Reformer open Thursday?" }
```

---

## Phase 2 — Vapi Voice Agent

Point a Vapi **Custom LLM** provider at `POST /chat`. The agent is already ready — same endpoint, no code changes. See `architecture.md` for details.

---

## Google Sheets Structure

| Tab | Purpose |
|-----|---------|
| Contacts | One row per caller: phone, name, email, call count, dates |
| Bookings | Every booking with event_id, status (active/cancelled) |
| CallLog | Every conversation: summary, actions taken, escalated flag |

## What the agent handles vs escalates

| Scenario | Handled by agent |
|---|---|
| Book / reschedule / cancel a class | ✅ |
| Pricing & hours questions | ✅ |
| Drop-in enquiries | ✅ |
| Birthday party info | ✅ |
| Caller running late | ✅ |
| Billing dispute / charge complaint | ❌ → escalated to human |
| Membership / contract issues | ❌ → escalated to human |
