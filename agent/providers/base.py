"""
Canonical message format (Anthropic-style plain dicts) and provider abstraction.

All providers translate TO and FROM this canonical format at their boundary.
The session message history always stays in canonical format.

Canonical message shapes:
  # Simple text turn
  {"role": "user"|"assistant", "content": "text"}

  # Assistant with tool calls (may also have text)
  {"role": "assistant", "content": [
      {"type": "text", "text": "..."},                               # optional
      {"type": "tool_use", "id": "tc_1", "name": "fn", "input": {}},
  ]}

  # Tool results (user turn following an assistant tool-use turn)
  {"role": "user", "content": [
      {"type": "tool_result", "tool_use_id": "tc_1",
       "tool_name": "fn",          # extra field used by Gemini
       "content": "<json string>"},
  ]}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMProvider(ABC):
    """Abstract provider. Subclasses translate canonical ↔ native formats."""

    @abstractmethod
    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Call the LLM and return a normalised LLMResponse."""
        ...

    # ------------------------------------------------------------------
    # These are provider-agnostic: they build canonical messages
    # that any provider's complete() will translate correctly.
    # ------------------------------------------------------------------

    @staticmethod
    def format_assistant_message(response: LLMResponse) -> dict:
        """Build a canonical assistant message from an LLMResponse."""
        blocks: list[dict] = []
        if response.text:
            blocks.append({"type": "text", "text": response.text})
        for tc in response.tool_calls:
            blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})

        if not blocks:
            # Fall-back: plain text turn (empty response edge case)
            return {"role": "assistant", "content": response.text or ""}
        if len(blocks) == 1 and blocks[0]["type"] == "text":
            return {"role": "assistant", "content": blocks[0]["text"]}
        return {"role": "assistant", "content": blocks}

    @staticmethod
    def format_tool_result_message(results: list[dict]) -> dict:
        """Build a canonical tool-results message from a list of result dicts.

        Each result dict must have: tool_use_id, tool_name, content (json string).
        """
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r["tool_use_id"],
                    "tool_name": r["tool_name"],
                    "content": r["content"],
                }
                for r in results
            ],
        }
