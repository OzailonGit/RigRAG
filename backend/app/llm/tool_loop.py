"""Generic tool-calling loop: the LLM picks which tool(s) to call, results
from real data are fed back, and the loop repeats until the model answers
in plain text or the iteration cap is reached.

Messages follow the OpenAI tool-calling convention (id-addressed tool
results), which both providers' `chat()` implementations accept; the
Ollama client maps it onto its own internal shape.
"""

import json
import logging

from app.json_utils import to_prompt_json
from app.llm.providers.base import LLMProvider
from app.llm.tools import TOOL_DEFINITIONS, ToolExecutionError, ToolExecutor

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 4
_GAVE_UP = "I wasn't able to fully answer that after several tool calls. Could you rephrase or narrow the question?"


def run_tool_loop(llm: LLMProvider, executor: ToolExecutor, messages: list[dict]) -> str:
    messages = list(messages)  # don't mutate the caller's list

    for iteration in range(MAX_ITERATIONS):
        # The last round offers no tools, so the model answers from the
        # lookups it already made instead of being cut off mid-search.
        last_round = iteration == MAX_ITERATIONS - 1
        response = llm.chat(messages, tools=None if last_round else TOOL_DEFINITIONS)

        if not response.tool_calls:
            return response.content or _GAVE_UP

        messages.append({
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in response.tool_calls
            ],
        })

        for tc in response.tool_calls:
            try:
                result = executor.execute(tc.name, tc.arguments)
            except ToolExecutionError as exc:
                result = {"error": str(exc)}
            except Exception as exc:
                logger.warning("Tool %s raised an unexpected error: %s", tc.name, exc)
                result = {"error": f"Tool execution failed: {exc}"}

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.name,
                "content": to_prompt_json(result),
            })

    return _GAVE_UP
