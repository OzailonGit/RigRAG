"""Ties intent parsing, the recommendation engine, and the tool-calling
loop into one conversational turn.

Each message is first classified (intent_parser.py). Small talk gets a
short fixed reply about what the assistant does, and off-topic messages get
OFF_TOPIC_REPLY; neither reaches the engine or the LLM's own answers, and
neither changes the conversation state.

Without a budget, the first turn quotes the cheapest build that fully
covers the request (starting_budget.py) and asks for a budget. If the next
message still has none ("not sure", "you pick"), that build is shown.

Otherwise a turn takes one of three paths:

1. Build request: always runs parse_message -> RecommendationEngine.recommend
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
from app.llm.intent_parser import parse_message
from app.llm.providers.base import LLMProvider
from app.llm.tool_loop import run_tool_loop
from app.llm.tools import ToolExecutor
from app.recommendation.engine import BuildResult, RecommendationEngine
from app.recommendation.starting_budget import StartingBudget, find_starting_budget
from app.schemas import BuildRequest, MessageKind, UseCase
from app.services.chat_sessions import ChatSession

MIN_VALID_BUDGET = 500  # below this, treat budget_php as "not really given yet"

OFF_TOPIC_REPLY = "I can only help with picking PC parts and builds."
_GREETING_REPLY = (
    "Hi! I put together PC builds from a catalog of real parts, checked for compatibility. "
    "Tell me your budget in pesos and what you'll use the PC for (games, resolution, editing, and so on)."
)
_FOLLOWUP_PROMPT_REPLY = (
    "Happy to help. You can ask about any part in this build, compare it with others, "
    "or change the budget or preferences."
)

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


def _describe_request(state: BuildRequest) -> str:
    """"1080p gaming with Wi-Fi", built only from fields the user set."""
    uses = " and ".join(uc.value.replace("_", " ").replace("ai ml", "AI/ML") for uc in state.use_case
                        if uc is not UseCase.mixed) or "general use"
    text = f"{state.resolution.value} {uses}" if state.resolution else uses
    extras = []
    if state.cpu_brand:
        extras.append(f"an {state.cpu_brand} CPU")
    if state.gpu_brand:
        extras.append(f"an {state.gpu_brand} GPU")
    if state.wifi_required:
        extras.append("Wi-Fi")
    if state.small_form_factor:
        extras.append("a small case")
    return f"{text} with {', '.join(extras)}" if extras else text


def _offer_starting_budget(state: BuildRequest, starting: StartingBudget) -> str:
    return (
        f"For {_describe_request(state)}, the cheapest build that fully covers it comes to about "
        f"₱{starting.price_php:,.0f} with current prices. Do you have a budget in mind? "
        "If you're not sure, just say so and I'll put that build together."
    )


def _build_at_starting_budget(llm: LLMProvider, session: ChatSession, state: BuildRequest,
                              starting: StartingBudget) -> ChatTurnResult:
    state = state.model_copy(update={"budget_php": starting.budget_php})
    session.state = state
    session.last_result = starting.result
    intro = (
        f"Since you don't have a budget yet, here's the cheapest build that fully covers "
        f"{_describe_request(state)}, at ₱{starting.price_php:,.0f}. Give me a budget and I'll adjust it."
    )
    return ChatTurnResult(reply=f"{intro}\n\n{explain_build(llm, starting.result, state)}",
                          state=state, build=starting.result)


def handle_chat_message(db: Session, llm: LLMProvider, session: ChatSession, message: str) -> ChatTurnResult:
    previous_state = session.state
    awaiting_budget = previous_state is not None and previous_state.budget_php < MIN_VALID_BUDGET
    parsed = parse_message(
        llm, message, existing=session.state,
        has_build=session.last_result is not None, awaiting_budget=awaiting_budget,
    )

    # Neither path touches session.state, so a stray message can't change
    # the build or count as the "still no budget" turn. No build is attached,
    # so the UI doesn't repeat the parts table under the reply.
    if parsed.kind is MessageKind.off_topic:
        return ChatTurnResult(reply=OFF_TOPIC_REPLY, state=previous_state or parsed.state, build=None)
    if parsed.kind is MessageKind.small_talk:
        reply = _FOLLOWUP_PROMPT_REPLY if session.last_result else _GREETING_REPLY
        return ChatTurnResult(reply=reply, state=previous_state or parsed.state, build=None)

    state = parsed.state
    session.state = state

    if state.budget_php < MIN_VALID_BUDGET:
        starting = find_starting_budget(db, state)
        if starting is None:
            return ChatTurnResult(reply=_ask_for_budget(llm, state), state=state, build=session.last_result)
        if not session.offered_starting_budget:
            session.offered_starting_budget = True
            return ChatTurnResult(reply=_offer_starting_budget(state, starting), state=state, build=session.last_result)
        return _build_at_starting_budget(llm, session, state, starting)

    wants_new_build = parsed.kind is MessageKind.build_request or session.last_result is None

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
            # IDs let the model call get_part_details on the parts it's asked about.
            "primary_parts": {
                role: {"id": str(part["id"]), "name": f"{part['manufacturer']} {part['model']}"}
                for role in ["cpu", "gpu", "motherboard", "ram", "psu", "cooler", "case"]
                if (part := getattr(session.last_result.primary.components, role))
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
                "If the question isn't about PC hardware or this build, don't answer it; reply only: "
                f"\"{OFF_TOPIC_REPLY}\" "
                f"Context: {context}"
            ),
        },
        {"role": "user", "content": message},
    ]
    reply = run_tool_loop(llm, executor, messages)
    return ChatTurnResult(reply=reply, state=state, build=session.last_result)
