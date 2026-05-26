# Solstice Pilates — AI Receptionist Architecture

## System Overview

```mermaid
graph TB
    subgraph Client["Client Layer"]
        UI["Chat UI\n(HTML / JS)"]
        VAPI["Vapi Voice Agent\n(Phase 2 — webhook)"]
    end

    subgraph API["API Layer  ·  FastAPI"]
        ROUTES["/chat  /session\n/health  /history"]
    end

    subgraph Agent["Agent Layer"]
        direction TB
        LOOP["Agent Loop\nPilatesAgent"]
        PROMPT["System Prompt\n+ Studio Context\n(prompts.py)"]
        DISPATCH["Tool Dispatcher"]
        SESSION["Session Manager\n(in-memory, swappable\nfor Redis in prod)"]
    end

    subgraph Tools["Tool Layer  ·  agent/tools/"]
        T_CAL["Calendar Tools\ncheck_availability\nlist_classes\nbook / reschedule / cancel"]
        T_SHT["Sheets Tools\nlog_call"]
        T_STU["Studio Tools\nget_studio_info\nescalate_to_human"]
    end

    subgraph Integrations["Integration Layer"]
        GCAL_CLIENT["GoogleCalendarClient\nintegrations/google_calendar.py"]
        GSHT_CLIENT["GoogleSheetsClient\nintegrations/google_sheets.py"]
    end

    subgraph Google["Google Cloud  ·  Service Account OAuth2"]
        GCAL[("Google Calendar\nClass Schedule\n(events + extendedProperties\nfor classType & maxCapacity)")]
        GSHEET[("Google Sheets\nContacts tab\nBookings tab\nCallLog tab")]
    end

    ANTHROPIC["Anthropic API\nClaude Sonnet"]

    %% Client → API
    UI   -->|"HTTP POST /chat"| ROUTES
    VAPI -->|"HTTP POST /chat\n(same endpoint)"| ROUTES

    %% API → Agent
    ROUTES --> SESSION
    ROUTES --> LOOP

    %% Agent internals
    LOOP --> PROMPT
    LOOP -->|"tool_use blocks"| DISPATCH
    LOOP -->|"messages []"| ANTHROPIC

    %% Dispatch → Tools
    DISPATCH --> T_CAL
    DISPATCH --> T_SHT
    DISPATCH --> T_STU

    %% Tools → Integrations
    T_CAL --> GCAL_CLIENT
    T_SHT --> GSHT_CLIENT
    T_STU -.->|"static data only"| LOOP

    %% Integrations → Google
    GCAL_CLIENT -->|"Calendar API v3"| GCAL
    GSHT_CLIENT -->|"Sheets API v4"| GSHEET

    %% Styling
    classDef external fill:#e8f5e9,stroke:#388e3c
    classDef agent   fill:#e3f2fd,stroke:#1565c0
    classDef google  fill:#fce4ec,stroke:#c62828
    classDef tool    fill:#fff8e1,stroke:#f9a825

    class ANTHROPIC,GCAL,GSHEET external
    class LOOP,PROMPT,DISPATCH,SESSION agent
    class GCAL_CLIENT,GSHT_CLIENT google
    class T_CAL,T_SHT,T_STU tool
```

---

## Data Flow — Booking a Class

```
Caller:  "Book me into the 7pm Reformer on Thursday"

1. POST /chat  →  FastAPI  →  PilatesAgent.chat()
2. Agent adds message to session history
3. Calls Anthropic API  →  Claude returns tool_use: check_class_availability
4. Tool: GoogleCalendarClient.find_class_event("reformer", "2025-06-05", "19:00")
         GoogleSheetsClient.get_booking_count(event_id)   → 7 / 10 booked
5. Claude sees 3 spots → asks caller for name + phone
6. Caller provides info  →  Claude returns tool_use: book_class
7. Tool: GoogleSheetsClient.create_booking(...)           → booking_id = "A1B2C3D4"
         GoogleSheetsClient.upsert_contact(phone, name)   → CRM updated
8. Claude confirms booking; conversation ends
9. Claude returns tool_use: log_call
10. Tool: GoogleSheetsClient.log_call(...)                → CallLog row written
11. Final text response returned to caller
```

---

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| LLM | Anthropic Claude (claude-sonnet-4-6) | Reliable tool_use, clean stop_reason API |
| Capacity tracking | Sheets Bookings tab, not Calendar attendees | Avoids email requirements; auditable |
| Session state | In-memory dict | Simple for Phase 1; swap key for Redis in prod |
| Auth | Google Service Account | No user-flow needed for a server-side app |
| Phase 2 extension | Same `/chat` POST endpoint | Vapi webhook hits the same route — no changes to agent |
| Escalation | `escalate_to_human` tool | Agent decides; logged + flagged for staff callback |

---

## Phase 2: Voice Agent (Vapi)

The agent is already Phase-2 ready:

```
Vapi inbound call
    └── Vapi sends POST /chat  { session_id, message: "<transcribed speech>" }
    └── Agent replies with text
    └── Vapi reads reply aloud via TTS
```

Steps to wire up:
1. Point a Vapi Custom LLM provider at `POST /chat`
2. Configure Vapi with the studio phone number
3. Tune `max_tokens` down to ~200 for snappier spoken replies
4. Optionally add a `/vapi/end-of-call` webhook to trigger `log_call` if the caller hangs up mid-session

No agent code changes required.

---

## Project Structure

```
solstice_pilates/
├── agent/
│   ├── agent.py          # PilatesAgent: loop + tool dispatch + session
│   ├── prompts.py        # System prompt (injected fresh each turn)
│   └── tools/
│       ├── calendar_tools.py   # Anthropic tool schemas — calendar ops
│       ├── sheets_tools.py     # Anthropic tool schema — log_call
│       └── studio_tools.py     # Anthropic tool schemas — info + escalate
├── integrations/
│   ├── google_calendar.py     # Calendar API client
│   └── google_sheets.py       # Sheets API client (Contacts / Bookings / CallLog)
├── api/
│   └── routes.py         # FastAPI: /chat  /session  /health
├── config/
│   ├── settings.py       # Pydantic-settings from .env
│   └── studio_info.py    # Static studio info dict
├── ui/
│   └── index.html        # Minimal Phase-1 chat UI
├── scripts/
│   └── setup_fixtures.py # Populates Calendar + Sheet for testing
├── tests/
│   └── test_agent.py     # Unit tests (offline, mocked)
├── credentials/          # Git-ignored; put service_account.json here
├── architecture.md       # This file
├── main.py               # App entry point (uvicorn)
├── requirements.txt
└── .env.example
```
