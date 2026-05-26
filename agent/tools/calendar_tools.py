"""Anthropic tool definitions for Google Calendar operations."""

CALENDAR_TOOLS: list[dict] = [
    {
        "name": "check_class_availability",
        "description": (
            "Check how many spots remain in a specific class. "
            "Call this before booking, or when a caller asks if a class is open."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "class_type": {
                    "type": "string",
                    "description": "Type of class",
                    "enum": ["reformer", "mat", "tower", "private"],
                },
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format, e.g. '2025-05-29'",
                },
                "time": {
                    "type": "string",
                    "description": "Time in HH:MM 24-hour format, e.g. '18:00' for 6 pm",
                },
            },
            "required": ["class_type", "date", "time"],
        },
    },
    {
        "name": "list_available_classes",
        "description": (
            "List upcoming classes that still have open spots. "
            "Use when a caller asks what classes are available or wants alternatives."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days ahead to look (default 7)",
                    "default": 7,
                },
                "class_type": {
                    "type": "string",
                    "description": "Filter to a specific class type, or 'all' for everything",
                    "enum": ["reformer", "mat", "tower", "private", "all"],
                    "default": "all",
                },
            },
            "required": [],
        },
    },
    {
        "name": "book_class",
        "description": (
            "Book a class for a caller. "
            "Only call this after (1) confirming availability and (2) collecting name + phone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "class_type": {
                    "type": "string",
                    "enum": ["reformer", "mat", "tower", "private"],
                },
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "HH:MM 24-hour"},
                "customer_name": {"type": "string"},
                "customer_phone": {"type": "string", "description": "Caller's phone number"},
            },
            "required": ["class_type", "date", "time", "customer_name", "customer_phone"],
        },
    },
    {
        "name": "find_customer_bookings",
        "description": "Look up active bookings for a caller by their phone number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_phone": {"type": "string"},
            },
            "required": ["customer_phone"],
        },
    },
    {
        "name": "reschedule_booking",
        "description": (
            "Move an existing booking to a different class time. "
            "Confirm the new slot is available before calling this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "The booking ID to reschedule"},
                "new_class_type": {
                    "type": "string",
                    "enum": ["reformer", "mat", "tower", "private"],
                },
                "new_date": {"type": "string", "description": "YYYY-MM-DD"},
                "new_time": {"type": "string", "description": "HH:MM 24-hour"},
            },
            "required": ["booking_id", "new_class_type", "new_date", "new_time"],
        },
    },
    {
        "name": "cancel_booking",
        "description": "Cancel an existing booking. Inform the caller about the cancellation policy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string"},
            },
            "required": ["booking_id"],
        },
    },
]
