"""Ties intent parsing, the recommendation engine, and the tool-calling
loop into one conversational turn.

A turn takes one of three paths:

1. Build request: always runs parse_intent -> RecommendationEngine.recommend
   -> explain_build. Build generation is never left to the model's choice
   of tool, so the engine cannot be bypassed by the model answering from
   its own knowledge.
2. What-if question ("what if I increase my budget by 10,000?"): when
   intent parsing shows a real constraint change, the engine re-runs under
   the new constraints and the LLM explains the diff against the previous
   build.
3. Exploratory follow-up (comparisons, "why not X", spec lookups): handled
   by the tool-calling loop, where the model picks which lookup answers
   the question.

In every path the engine selects the parts and the LLM only narrates.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.llm.explain import explain_build, explain_followup
from app.llm.intent_parser import parse_intent
from app.llm.providers.base import LLMProvider
from app.llm.tool_loop import run_tool_loop
from app.llm.tools import ToolExecutor
from app.recommendation.engine import BuildResult, RecommendationEngine
from app.schemas import BuildRequest
from app.services.chat_sessions import ChatSession

MIN_VALID_BUDGET = 500  # below this, treat budget_php as "not really given yet"

_BUILD_REQUEST_KEYWORDS = ("build", "recommend", "suggest", "put together", "what should i get", "pc for")

# Fields that describe what the recommendation engine should build. If any
# of these differ from the previous turn's state, the user has changed a
# real constraint (not just asked a question about the existing build) and
# the engine should be re-run rather than answered via tool lookup.
_ENGINE_RELEVANT_FIELDS = (
    "budget_php", "use_case", "resolution", "target_fps", "cpu_brand", "gpu_brand",
    "wifi_required", "small_form_factor", "quiet", "low_power",
    "prioritize_upgradeability", "existing_parts",
)


@dataclass
class ChatTurnResult:
    reply: str
    state: BuildRequest
    build: BuildResult | None


def _looks_like_build_request(message: str) -> bool:
    lowered = message.lower()
    return any(kw in lowered for kw in _BUILD_REQUEST_KEYWORDS)


def _engine_relevant_state_changed(old: BuildRequest | None, new: BuildRequest) -> bool:
    if old is None:
        return False
    return any(getattr(old, field) != getattr(new, field) for field in _ENGINE_RELEVANT_FIELDS)


def _ask_for_budget(llm: LLMProvider, state: BuildRequest | None) -> str:
    messages = [
        {
            "role": "system",
            "content": "You are a PC-building assistant. The user hasn't given a budget yet, which is required before you can recommend anything. Ask a single short, friendly question for their budget (mention PHP/pesos). Do not say anything else.",
        },
        {"role": "user", "content": "(no budget given yet)"},
    ]
    response = llm.chat(messages)
    return response.content or "What's your budget in PHP?"


def handle_chat_message(db: Session, llm: LLMProvider, session: ChatSession, message: str) -> ChatTurnResult:
    previous_state = session.state
    state = parse_intent(llm, message, existing=session.state)
    session.state = state

    if state.budget_php < MIN_VALID_BUDGET:
        return ChatTurnResult(reply=_ask_for_budget(llm, state), state=state, build=session.last_result)

    wants_new_build = _looks_like_build_request(message) or session.last_result is None

    if wants_new_build:
        result = RecommendationEngine.recommend(db, state)
        session.last_result = result
        if result is None:
            reply = (
                "I couldn't find a compatible build under these constraints. "
                "Try raising the budget or relaxing a preference (brand, Wi-Fi, form factor)."
            )
            return ChatTurnResult(reply=reply, state=state, build=None)
        reply = explain_build(llm, result, state)
        return ChatTurnResult(reply=reply, state=state, build=result)

    if _engine_relevant_state_changed(previous_state, state):
        # What-if question: a real constraint changed without an explicit request
        # for a new build. Re-run the engine and explain the diff.
        previous_result = session.last_result
        new_result = RecommendationEngine.recommend(db, state)
        if new_result is None:
            reply = (
                "That change doesn't leave a compatible build under the other constraints; "
                "try a smaller change or adjust a different preference."
            )
            return ChatTurnResult(reply=reply, state=state, build=previous_result)

        session.last_result = new_result
        reply = explain_followup(
            llm, message, previous_result, previous_state,
            comparison=new_result, comparison_request=state,
        )
        return ChatTurnResult(reply=reply, state=state, build=new_result)

    # Follow-up / exploratory question against the existing build: let the
    # model decide which tool(s) answer it, grounded in real data.
    executor = ToolExecutor(db)
    context = {
        "current_request": state.model_dump(mode="json"),
        "current_build_summary": {
            "primary_price_php": session.last_result.primary.score.total_price_php if session.last_result else None,
            "primary_parts": {
                role: (getattr(session.last_result.primary.components, role) or {}).get("model")
                for role in ["cpu", "gpu", "motherboard", "ram", "psu", "cooler", "case"]
            } if session.last_result else None,
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are a PC-building assistant answering a follow-up question. "
                "Use the provided tools to look up any fact you need. Never state a spec, price, or "
                "compatibility result you haven't retrieved via a tool call. "
                f"Context: {context}"
            ),
        },
        {"role": "user", "content": message},
    ]
    reply = run_tool_loop(llm, executor, messages)
    return ChatTurnResult(reply=reply, state=state, build=session.last_result)
