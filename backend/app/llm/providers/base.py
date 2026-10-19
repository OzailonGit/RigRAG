import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

_LEADING_THINK_BLOCK = re.compile(r"^\s*.*?</think>\s*", re.DOTALL)


def strip_thinking(content: str | None) -> str | None:
    """Strips a leaked <think>...</think> reasoning block from model output.

    qwen3 can emit its reasoning trace in `content` on tool-calling responses
    even with Ollama's `think=False`. Stripping it at the provider boundary
    gives every caller (tool loop, explanations, chat) clean content and keeps
    the reasoning out of conversation history, where it would grow the
    context on every tool-loop round-trip.
    """
    if content is None:
        return None
    if "</think>" in content:
        content = _LEADING_THINK_BLOCK.sub("", content, count=1)
    return content.strip() or None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider(ABC):
    """Abstraction over chat-completion-style LLM backends.

    The intent parser, explanation generator, and tool-calling loop depend
    only on this interface, so OllamaProvider, CloudProvider, or a custom
    OpenAI-compatible endpoint can be swapped freely. All implementations use
    the OpenAI message shape (`{"role": ..., "content": ...}`), which Ollama,
    Groq, and OpenRouter support natively.
    """

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a chat completion request.

        `tools`: OpenAI-style function-calling tool definitions. If the model
        calls one or more, they come back as `LLMResponse.tool_calls` and
        `content` may be None.

        `json_mode`: ask the model to return a raw JSON object as content (used
        for intent parsing). This does not guarantee schema conformance, so
        callers still validate the result.
        """
