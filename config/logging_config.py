"""
Logging configuration for Solstice Pilates AI Receptionist.

Two formats:
  pretty  — human-readable, for local development
  json    — structured JSON, for production log aggregation (Datadog / CloudWatch / ELK)

Usage:
    from config.logging_config import setup_logging
    setup_logging(level="INFO", fmt="pretty")

Environment variables:
    LOG_LEVEL   INFO | DEBUG | WARNING | ERROR   (default: INFO)
    LOG_FORMAT  pretty | json                    (default: pretty)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any


# ---------------------------------------------------------------------------
# Timer utility
# ---------------------------------------------------------------------------

class Timer:
    """
    Context manager that measures elapsed milliseconds.

        with Timer() as t:
            do_something()
        print(t.elapsed_ms)   # int
    """

    elapsed_ms: int = 0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        self.elapsed_ms = round((time.perf_counter() - self._start) * 1000)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

# Standard logging fields we never want to repeat in the structured output.
_LOGGING_INTERNALS = frozenset({
    "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "name", "message", "taskName",
})


class JSONFormatter(logging.Formatter):
    """
    Emits one JSON object per log line.

    Every extra field passed via logger.info("event", extra={key: val})
    becomes a top-level JSON field — making log queries like
    `level:INFO AND event:tool.end AND duration_ms:>500` work in any
    log aggregation tool.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        ts = self.formatTime(record, "%Y-%m-%dT%H:%M:%S")

        doc: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "call_id": getattr(record, "call_id", "-"),
            "session_id": getattr(record, "session_id", "-"),
            "channel": getattr(record, "channel", "-"),
            "msg": record.message,
        }

        # Merge any extra={} fields set by the caller
        for key, value in record.__dict__.items():
            if key not in _LOGGING_INTERNALS and not key.startswith("_") and key not in doc:
                doc[key] = value

        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)

        return json.dumps(doc, default=str)


class PrettyFormatter(logging.Formatter):
    """
    Human-readable single-line format for development.

    Example:
        10:15:30.312  INFO     [call_abc123/voice    ]  voice.first_token              latency_ms=312
        10:15:30.831  INFO     [call_abc123/voice    ]  llm.end                        duration_ms=685  stop_reason=tool_use  tool_calls=1
        10:15:30.832  INFO     [call_abc123/voice    ]  tool.start                     tool=check_class_availability
        10:15:31.015  INFO     [call_abc123/voice    ]  tool.end                       tool=check_class_availability  duration_ms=183  success=True
        10:15:31.559  INFO     [call_abc123/voice    ]  voice.turn.end                 total_ms=1414  chars_streamed=79  iterations=2
    """

    MSG_WIDTH = 30

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        # Millisecond-precision timestamp
        ts = self.formatTime(record, "%H:%M:%S") + f".{int(record.msecs):03d}"

        call_id = getattr(record, "call_id", "-")
        channel = getattr(record, "channel", "-")
        ctx = f"{call_id}/{channel}"

        # Extra fields set by the caller
        skip = _LOGGING_INTERNALS | {"call_id", "session_id", "channel", "message"}
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in skip and not k.startswith("_")
        }
        extra_str = "  ".join(f"{k}={v}" for k, v in extras.items())

        line = (
            f"{ts}  "
            f"{record.levelname:<8}  "
            f"[{ctx:<26}]  "
            f"{record.message:<{self.MSG_WIDTH}}  "
            f"{extra_str}"
        )

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line.rstrip()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO", fmt: str = "pretty") -> None:
    """
    Configure the root logger.  Call once at application startup.

    level : "DEBUG" | "INFO" | "WARNING" | "ERROR"
    fmt   : "pretty" | "json"
    """
    from config.log_context import ContextFilter

    formatter: logging.Formatter = (
        JSONFormatter() if fmt.lower() == "json" else PrettyFormatter()
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Silence noisy third-party libraries
    for name in (
        "googleapiclient",
        "google.auth",
        "google.auth.transport",
        "urllib3",
        "httpx",
        "httpcore",
        "anthropic",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
