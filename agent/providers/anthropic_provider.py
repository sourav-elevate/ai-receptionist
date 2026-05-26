"""Anthropic Claude provider. Canonical format IS Anthropic format — minimal translation."""

from __future__ import annotations

import anthropic

from agent.providers.base import LLMProvider, LLMResponse, ToolCall


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        # Canonical format == Anthropic format — pass through directly.
        # Strip the extra `tool_name` field Anthropic doesn't need.
        cleaned = self._strip_tool_name(messages)

        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=cleaned,
        )

        text: str | None = None
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if hasattr(block, "text"):
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

        return LLMResponse(text=text, tool_calls=tool_calls)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_tool_name(messages: list[dict]) -> list[dict]:
        """Remove the extra `tool_name` key from tool_result blocks."""
        result = []
        for msg in messages:
            content = msg["content"]
            if isinstance(content, list):
                clean_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        clean_blocks.append({k: v for k, v in block.items() if k != "tool_name"})
                    else:
                        clean_blocks.append(block)
                result.append({**msg, "content": clean_blocks})
            else:
                result.append(msg)
        return result
