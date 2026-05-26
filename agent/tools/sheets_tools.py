"""Anthropic tool definition for call logging via Google Sheets."""

SHEETS_TOOLS: list[dict] = [
    {
        "name": "log_call",
        "description": (
            "Log the outcome of this conversation to the CRM. "
            "ALWAYS call this once before ending every conversation, "
            "even if the caller hung up or nothing was resolved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_phone": {
                    "type": "string",
                    "description": "Caller's phone number. Use 'unknown' if never provided.",
                },
                "customer_name": {
                    "type": "string",
                    "description": "Caller's name. Use 'unknown' if never provided.",
                },
                "summary": {
                    "type": "string",
                    "description": "One or two sentence summary of the call.",
                },
                "actions_taken": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of actions taken. Examples: "
                        "'booked_class', 'rescheduled_booking', 'cancelled_booking', "
                        "'provided_pricing', 'provided_hours', 'late_arrival_noted', "
                        "'drop_in_info_given', 'birthday_party_info_given', 'escalated_to_human'"
                    ),
                },
                "escalated": {
                    "type": "boolean",
                    "description": "True if the call was handed off to a human.",
                },
                "escalation_reason": {
                    "type": "string",
                    "description": "Why escalation was needed (leave empty if not escalated).",
                },
            },
            "required": ["customer_phone", "customer_name", "summary", "actions_taken", "escalated"],
        },
    },
]
