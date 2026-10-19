from typing import Any

import ollama

from app.config import get_settings
from app.llm.providers.base import LLMProvider, LLMResponse, ToolCall, strip_thinking


class OllamaProvider(LLMProvider):
    """Default local LLM provider.

    `think=False` is passed explicitly because qwen3 otherwise emits a
    <think>...</think> block before its answer, which breaks JSON parsing for
    intent extraction and tool arguments. Reliable structured output matters
    more here than visible reasoning.
    """

    def __init__(self, model: str | None = None, base_url: str | None = None):
        settings = get_settings()
        self._model = model or settings.ollama_model
        self._client = ollama.Client(host=base_url or settings.ollama_base_url)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        response = self._client.chat(
            model=self._model,
            messages=messages,
            tools=tools,
            format="json" if json_mode else None,
            think=False,
            options={"temperature": 0.2},
        )

        message = response["message"]
        tool_calls = [
            ToolCall(id=f"call_{i}", name=tc["function"]["name"], arguments=dict(tc["function"]["arguments"]))
            for i, tc in enumerate(message.get("tool_calls") or [])
        ]
        return LLMResponse(content=strip_thinking(message.get("content")), tool_calls=tool_calls)
