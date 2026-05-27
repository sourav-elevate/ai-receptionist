"""
Vapi assistant configuration.

All the latency-critical knobs are set here and documented.
Run scripts/create_vapi_assistant.py to push this config to Vapi.
"""

from __future__ import annotations


def get_assistant_config(
    server_base_url: str,
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM",  # Rachel — warm, calm female
) -> dict:
    """
    Returns the full Vapi assistant configuration dict.

    server_base_url: e.g. "https://your-ngrok-url.ngrok.io" (no trailing slash)
    elevenlabs_voice_id: ElevenLabs voice to use for TTS
    """
    custom_llm_url = f"{server_base_url}/vapi/chat"
    webhook_url = f"{server_base_url}/vapi/webhook"

    return {
        "name": "Solstice Pilates Receptionist",

        # ── STT (Speech → Text) ────────────────────────────────────────
        # Deepgram nova-2-phonecall: trained on phone audio, ~150ms latency.
        # smartFormat off: we do our own number normalisation.
        # keywords: boost recognition of studio-specific terms.
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2-phonecall",
            "language": "en-US",
            "smartFormat": False,
            "keywords": [
                "Reformer:2",
                "Pilates:2",
                "Solstice:2",
                "reformer:2",
                "pilates:2",
                "solstice:2",
            ],
        },

        # ── LLM ────────────────────────────────────────────────────────
        # Custom LLM = our FastAPI server.  Vapi sends requests here in
        # OpenAI chat-completion format and reads the SSE stream back.
        # temperature 0.5: consistent but not robotic.
        # maxTokens 300: voice answers are short; keep first-token latency tight.
        "model": {
            "provider": "custom-llm",
            "url": custom_llm_url,
            "model": "solstice-v1",
            "temperature": 0.5,
            "maxTokens": 300,
            "urlRequestMetadataEnabled": True,   # include call metadata in request
        },

        # ── TTS (Text → Speech) ────────────────────────────────────────
        # ElevenLabs turbo_v2_5: lowest-latency 11labs model (~200ms to first audio).
        # optimizeStreamingLatency 4: maximum latency reduction (slight quality trade-off).
        # stability 0.45: slightly less stable = more natural variation.
        # similarityBoost 0.75: maintain voice identity across turns.
        # useSpeakerBoost false: not needed for phone calls.
        "voice": {
            "provider": "11labs",
            "voiceId": elevenlabs_voice_id,
            "model": "eleven_turbo_v2_5",
            "stability": 0.45,
            "similarityBoost": 0.75,
            "style": 0.0,
            "useSpeakerBoost": False,
            "optimizeStreamingLatency": 4,
        },

        # ── Turn-taking & timing ───────────────────────────────────────
        # responseDelaySeconds 0.1: tiny pause after caller stops speaking —
        #   feels natural, prevents cutting off slow speakers.
        # llmRequestDelaySeconds 0: fire the LLM request immediately.
        # silenceTimeoutSeconds 30: hang up after 30 s of silence.
        # maxDurationSeconds 600: cap calls at 10 minutes.
        "responseDelaySeconds": 0.1,
        "llmRequestDelaySeconds": 0.0,
        "silenceTimeoutSeconds": 30,
        "maxDurationSeconds": 600,

        # ── Interruptions ─────────────────────────────────────────────
        # interruptionsEnabled: let the caller cut in while agent is speaking.
        # numWordsToInterruptAssistant 1: interrupt after 1 word — very responsive.
        "interruptionsEnabled": True,
        "numWordsToInterruptAssistant": 1,

        # ── Audio quality ─────────────────────────────────────────────
        # backgroundDenoisingEnabled: clean up caller-side noise (phone / wind etc).
        "backgroundDenoisingEnabled": True,

        # ── Streaming ─────────────────────────────────────────────────
        # modelOutputInRealTimeEnabled: Vapi plays TTS as tokens arrive
        #   rather than waiting for the full sentence.  Critical for latency.
        "modelOutputInRealTimeEnabled": True,

        # ── Opening & closing ─────────────────────────────────────────
        "firstMessage": "Thanks for calling Solstice Pilates! How can I help you?",
        "firstMessageMode": "assistant-speaks-first",
        "endCallMessage": "Thanks for calling Solstice Pilates, talk soon!",
        "endCallPhrases": [
            "goodbye",
            "bye bye",
            "see you",
            "talk soon",
            "have a good one",
        ],

        # ── Webhook ───────────────────────────────────────────────────
        # end-of-call-report: triggers our fallback log_call if the agent
        # didn't get to it (caller hung up early, etc).
        "serverUrl": webhook_url,
        "serverMessages": ["end-of-call-report", "hang"],
    }
