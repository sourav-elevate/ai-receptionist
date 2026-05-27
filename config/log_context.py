"""
Call/session context that propagates through async and thread boundaries.

Python's contextvars are automatically inherited by:
  - child coroutines (await)
  - asyncio tasks (create_task)
  - threads spawned via asyncio.to_thread

This means we set the context once at the route handler, and every log line
emitted anywhere down the call stack — agent, voice agent, sheets client,
calendar client — automatically carries the same call_id and channel.
"""

from __future__ import annotations

import contextvars
import logging

_call_id: contextvars.ContextVar[str] = contextvars.ContextVar("call_id", default="-")
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="-")
_channel: contextvars.ContextVar[str] = contextvars.ContextVar("channel", default="-")


def bind(
    call_id: str | None = None,
    session_id: str | None = None,
    channel: str | None = None,
) -> None:
    """Set context for the current async task / thread."""
    if call_id is not None:
        _call_id.set(call_id)
    if session_id is not None:
        _session_id.set(session_id)
    if channel is not None:
        _channel.set(channel)


def get_call_id() -> str:
    return _call_id.get()


def get_session_id() -> str:
    return _session_id.get()


def get_channel() -> str:
    return _channel.get()


class ContextFilter(logging.Filter):
    """
    Injects call_id / session_id / channel into every LogRecord.
    Attach this filter to the root handler once at startup.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.call_id = _call_id.get()
        record.session_id = _session_id.get()
        record.channel = _channel.get()
        return True
