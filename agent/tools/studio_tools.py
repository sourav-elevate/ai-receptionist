"""Anthropic tool definitions for static studio information and human escalation."""

STUDIO_TOOLS: list[dict] = [
    {
        "name": "get_studio_info",
        "description": "Retrieve studio information: pricing, hours, policies, class descriptions, contact details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Which piece of information to return",
                    "enum": [
                        "pricing",
                        "hours",
                        "cancellation_policy",
                        "class_types",
                        "drop_in_policy",
                        "birthday_parties",
                        "late_arrivals",
                        "contact",
                        "all",
                    ],
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Flag this conversation for a human staff member to follow up. "
            "Use for: billing disputes or charge complaints, "
            "membership or contract issues, anything requiring management approval, "
            "or any situation you cannot confidently resolve."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Clear description of why a human is needed.",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "How urgent is this. 'high' means call back today.",
                },
                "customer_phone": {"type": "string"},
                "customer_name": {"type": "string"},
            },
            "required": ["reason", "urgency", "customer_phone"],
        },
    },
]
