"""OpenAI GPT provider. Translates canonical messages ↔ OpenAI chat format."""

from __future__ import annotations

import json

import openai

from agent.providers.base import LLMProvider, LLMResponse, ToolCall


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o") -> None:
        self._client = openai.OpenAI(api_key=api_key)
        self.model = model

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        oai_messages = self._to_openai_messages(system, messages)
        oai_tools = self._to_openai_tools(tools)

        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=oai_messages,
            tools=oai_tools,
            tool_choice="auto",
        )

        choice = response.choices[0]
        msg = choice.message
        text: str | None = msg.content or None
        tool_calls: list[ToolCall] = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=json.loads(tc.function.arguments),
                    )
                )

        return LLMResponse(text=text, tool_calls=tool_calls)

    # ------------------------------------------------------------------
    # Canonical → OpenAI translation
    # ------------------------------------------------------------------

    @staticmethod
    def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
        result: list[dict] = [{"role": "system", "content": system}]

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if isinstance(content, str):
                result.append({"role": role, "content": content})
                continue

            if role == "assistant":
                # Extract text blocks and tool_use blocks
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        tool_calls.append(
                            {
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block["input"]),
                                },
                            }
                        )
                oai_msg: dict = {
                    "role": "assistant",
                    "content": " ".join(text_parts) if text_parts else None,
                }
                if tool_calls:
                    oai_msg["tool_calls"] = tool_calls
                result.append(oai_msg)

            elif role == "user":
                # May be plain tool_result blocks
                for block in content:
                    if block.get("type") == "tool_result":
                        result.append(
                            {
                                "role": "tool",
                                "tool_call_id": block["tool_use_id"],
                                "content": block["content"],
                            }
                        )

        return result

    @staticmethod
    def _to_openai_tools(tools: list[dict]) -> list[dict]:
        """Convert Anthropic-style tool schemas to OpenAI function format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]
