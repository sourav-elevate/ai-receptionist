"""Google Gemini provider via the google-genai SDK (unified, non-deprecated)."""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types

from agent.providers.base import LLMProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)

# JSON Schema type → Gemini uppercase type
_TYPE_MAP = {
    "object": "OBJECT",
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
}


def _convert_schema(schema: dict) -> dict:
    """Recursively convert JSON Schema lowercase types to Gemini uppercase types."""
    result: dict[str, Any] = {}
    if "type" in schema:
        result["type"] = _TYPE_MAP.get(schema["type"], schema["type"].upper())
    if "description" in schema:
        result["description"] = schema["description"]
    if "enum" in schema:
        result["enum"] = schema["enum"]
    if "properties" in schema:
        result["properties"] = {k: _convert_schema(v) for k, v in schema["properties"].items()}
    if "required" in schema:
        result["required"] = schema["required"]
    if "items" in schema:
        result["items"] = _convert_schema(schema["items"])
    if "default" in schema:
        result["default"] = schema["default"]
    return result


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        self._client = genai.Client(api_key=api_key)
        self.model = model

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        contents = self._to_gemini_contents(messages)
        gemini_tools = self._to_gemini_tools(tools)

        response = self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=gemini_tools,
                max_output_tokens=max_tokens,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )

        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Canonical → Gemini translation
    # ------------------------------------------------------------------

    @staticmethod
    def _to_gemini_contents(messages: list[dict]) -> list[types.Content]:
        contents: list[types.Content] = []

        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            content = msg["content"]

            if isinstance(content, str):
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=content)]))
                continue

            parts: list[types.Part] = []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    parts.append(types.Part.from_text(text=block["text"]))
                elif btype == "tool_use":
                    parts.append(
                        types.Part.from_function_call(
                            name=block["name"],
                            args=block["input"],
                        )
                    )
                elif btype == "tool_result":
                    # Gemini needs a dict response, not a JSON string
                    try:
                        response_body = json.loads(block["content"])
                    except (json.JSONDecodeError, TypeError):
                        response_body = {"result": block["content"]}

                    parts.append(
                        types.Part.from_function_response(
                            name=block["tool_name"],
                            response=response_body,
                        )
                    )

            if parts:
                contents.append(types.Content(role=role, parts=parts))

        return contents

    @staticmethod
    def _to_gemini_tools(tools: list[dict]) -> list[types.Tool]:
        """Convert Anthropic tool schemas to Gemini Tool objects."""
        declarations = [
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=_convert_schema(t["input_schema"]),
            )
            for t in tools
        ]
        return [types.Tool(function_declarations=declarations)]

    # ------------------------------------------------------------------
    # Gemini response → LLMResponse
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        text: str | None = None
        tool_calls: list[ToolCall] = []

        try:
            candidate = response.candidates[0]
        except (IndexError, AttributeError):
            logger.warning("Gemini returned no candidates")
            return LLMResponse(text=None, tool_calls=[])

        for part in candidate.content.parts:
            # Text part
            if hasattr(part, "text") and part.text:
                text = part.text
            # Function call part
            elif part.function_call:
                fc = part.function_call
                # args is a MapComposite — normalise to plain dict
                args: dict = dict(fc.args) if hasattr(fc.args, "items") else (fc.args or {})
                tool_calls.append(
                    ToolCall(
                        id=f"gemini_{fc.name}_{len(tool_calls)}",
                        name=fc.name,
                        input=args,
                    )
                )

        return LLMResponse(text=text, tool_calls=tool_calls)
