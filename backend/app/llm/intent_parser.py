import json
import logging

from pydantic import ValidationError

from app.llm.prompts.system_prompts import INTENT_PARSER_SYSTEM
from app.llm.providers.base import LLMProvider
from app.llm.rule_based_intent import parse_intent_rule_based
from app.schemas import BuildRequest

logger = logging.getLogger(__name__)


def _parse_with_llm(llm: LLMProvider, message: str, existing: BuildRequest | None) -> BuildRequest:
    current_state = existing.model_dump(mode="json") if existing else {}
    messages = [
        {"role": "system", "content": INTENT_PARSER_SYSTEM},
        {"role": "user", "content": f"CURRENT STATE:\n{json.dumps(current_state)}\n\nNEWEST MESSAGE:\n{message}"},
    ]
    response = llm.chat(messages, json_mode=True)
    if not response.content:
        raise ValueError("LLM returned no content for intent parsing")

    data = json.loads(response.content)
    return BuildRequest(**data)


def parse_intent(llm: LLMProvider, message: str, existing: BuildRequest | None = None) -> BuildRequest:
    """Turns a user message and the prior conversational state into a
    BuildRequest.

    Tries the LLM first and falls back to conservative rule-based extraction
    when the LLM is unreachable or returns output that does not validate,
    instead of failing the request.
    """
    try:
        return _parse_with_llm(llm, message, existing)
    except (ValidationError, json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("LLM intent parsing failed (%s), falling back to rule-based extraction", exc)
    except Exception as exc:  # LLM provider network/connection errors, etc.
        logger.warning("LLM provider call failed (%s), falling back to rule-based extraction", exc)

    return parse_intent_rule_based(message, existing)
