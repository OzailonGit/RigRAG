import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError

from app.llm.prompts.system_prompts import INTENT_PARSER_SYSTEM
from app.llm.providers.base import LLMProvider
from app.llm.rule_based_intent import classify_message_rule_based, parse_intent_rule_based
from app.schemas import BuildRequest, MessageKind

logger = logging.getLogger(__name__)

# Stands in for "no budget given yet"; the orchestrator treats any budget
# below MIN_VALID_BUDGET as unknown.
PLACEHOLDER_BUDGET = 1


@dataclass
class ParsedMessage:
    state: BuildRequest
    kind: MessageKind


def _parse_with_llm(
    llm: LLMProvider, message: str, existing: BuildRequest | None, awaiting_budget: bool
) -> tuple[BuildRequest, MessageKind | None]:
    current_state = existing.model_dump(mode="json") if existing else {}
    waiting = "\nTHE ASSISTANT JUST ASKED FOR: a budget\n" if awaiting_budget else ""
    messages = [
        {"role": "system", "content": INTENT_PARSER_SYSTEM},
        {"role": "user", "content": f"CURRENT STATE:\n{json.dumps(current_state)}\n{waiting}\nNEWEST MESSAGE:\n{message}"},
    ]
    response = llm.chat(messages, json_mode=True)
    if not response.content:
        raise ValueError("LLM returned no content for intent parsing")

    data = json.loads(response.content)
    raw_kind = data.pop("message_kind", None)
    try:
        kind = MessageKind(raw_kind) if raw_kind else None
    except ValueError:
        kind = None  # an unknown label falls back to rule-based classification
    try:
        return BuildRequest(**data), kind
    except ValidationError:
        # For small talk and off-topic messages the state is ignored, and
        # models often return only the kind when the current state is empty.
        if kind in (MessageKind.small_talk, MessageKind.off_topic):
            return existing or BuildRequest(budget_php=PLACEHOLDER_BUDGET), kind
        raise


def parse_message(
    llm: LLMProvider,
    message: str,
    existing: BuildRequest | None = None,
    has_build: bool = False,
    awaiting_budget: bool = False,
) -> ParsedMessage:
    """Turns a user message and the prior conversational state into a
    BuildRequest plus the kind of message it is, in one LLM call.

    Falls back to conservative rule-based extraction when the LLM is
    unreachable or returns output that does not validate, and to rule-based
    classification when the LLM omits the message kind, instead of failing
    the request.
    """
    try:
        state, kind = _parse_with_llm(llm, message, existing, awaiting_budget)
        return ParsedMessage(state, kind or classify_message_rule_based(message, has_build, awaiting_budget))
    except (ValidationError, json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("LLM intent parsing failed (%s), falling back to rule-based extraction", exc)
    except Exception as exc:  # LLM provider network/connection errors, etc.
        logger.warning("LLM provider call failed (%s), falling back to rule-based extraction", exc)

    kind = classify_message_rule_based(message, has_build, awaiting_budget)
    try:
        state = parse_intent_rule_based(message, existing)
    except ValueError:
        # First message with no budget (or not about PCs at all).
        state = BuildRequest(budget_php=PLACEHOLDER_BUDGET)
    return ParsedMessage(state, kind)
