import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError

from app.json_utils import to_prompt_json
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


def _conversation_block(history: list[dict], pinnable: list[dict]) -> str:
    text = ""
    if history:
        lines = "\n".join(f"{m['role']}: {m['content']}" for m in history)
        text += f"\nRECENT CONVERSATION (oldest first):\n{lines}\n"
    if pinnable:
        lines = "\n".join(f"- {p['category']}: {p['name']} (id {p['id']}, {p['where']})" for p in pinnable)
        text += f"\nPARTS THAT CAN BE PINNED:\n{lines}\n"
    return text


def _parse_with_llm(
    llm: LLMProvider, message: str, existing: BuildRequest | None, awaiting_budget: bool,
    history: list[dict], pinnable: list[dict],
) -> tuple[BuildRequest, MessageKind | None]:
    current_state = existing.model_dump(mode="json") if existing else {}
    waiting = "\nTHE ASSISTANT JUST ASKED FOR: a budget\n" if awaiting_budget else ""
    context = _conversation_block(history, pinnable)
    messages = [
        {"role": "system", "content": INTENT_PARSER_SYSTEM},
        {"role": "user", "content": f"CURRENT STATE:\n{to_prompt_json(current_state)}\n{context}{waiting}\nNEWEST MESSAGE:\n{message}"},
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
    history: list[dict] | None = None,
    pinnable: list[dict] | None = None,
) -> ParsedMessage:
    """Turns a user message and the prior conversational state into a
    BuildRequest plus the kind of message it is, in one LLM call.

    `history` (recent {"role", "content"} messages) lets references like
    "switch to it" resolve. `pinnable` lists the parts, as {"id",
    "category", "name", "where"}, that the message may ask to pin; the
    caller still checks any pinned ID against it.

    Falls back to conservative rule-based extraction when the LLM is
    unreachable or returns output that does not validate, and to rule-based
    classification when the LLM omits the message kind, instead of failing
    the request.
    """
    try:
        state, kind = _parse_with_llm(llm, message, existing, awaiting_budget, history or [], pinnable or [])
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
