import json
import re
import time
from typing import Any

import httpx

from app.llm.providers.base import LLMProvider, LLMResponse, ToolCall, strip_thinking

_DURATION_RE = re.compile(r"([\d.]+)(ms|s|m|h)")
_DURATION_UNIT_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
MAX_RETRY_WAIT_SECONDS = 30
MAX_ATTEMPTS = 2  # 1 retry: free-tier rate limits are expected, not exceptional


def _parse_duration(value: str) -> float:
    """Parses Groq-style rate-limit reset durations like "7m12s" or
    "28.822s" into seconds."""
    return sum(float(amount) * _DURATION_UNIT_SECONDS[unit] for amount, unit in _DURATION_RE.findall(value))


class CloudProvider(LLMProvider):
    """OpenAI-compatible chat-completions client.

    Works unmodified against Groq, OpenRouter, or any other OpenAI-compatible
    endpoint; only base_url, api_key, and model change between them.

    Retries once on HTTP 429. Free-tier limits are easy to hit (Groq caps
    gpt-oss-120b at 8,000 tokens/minute, which one verbose explain_build()
    payload can approach), and a single capped retry turns a transient rate
    limit into a few seconds of latency instead of a failed request.
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        payload: dict[str, Any] = {"model": self._model, "messages": messages, "temperature": 0.2}
        if tools:
            payload["tools"] = tools
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = self._post_with_retry(payload)
        message = response.json()["choices"][0]["message"]

        tool_calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=json.loads(tc["function"]["arguments"]),
            )
            for tc in (message.get("tool_calls") or [])
        ]
        return LLMResponse(content=strip_thinking(message.get("content")), tool_calls=tool_calls)

    def _post_with_retry(self, payload: dict[str, Any]) -> httpx.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=60,
            )
            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                time.sleep(self._retry_wait_seconds(response))
                continue
            response.raise_for_status()
            return response
        raise AssertionError("unreachable: MAX_ATTEMPTS must be >= 1")

    @staticmethod
    def _retry_wait_seconds(response: httpx.Response) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), MAX_RETRY_WAIT_SECONDS)
            except ValueError:
                pass
        reset_tokens = response.headers.get("x-ratelimit-reset-tokens")
        if reset_tokens:
            return min(_parse_duration(reset_tokens), MAX_RETRY_WAIT_SECONDS)
        return 5.0
