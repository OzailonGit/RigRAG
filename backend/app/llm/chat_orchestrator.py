"""Ties intent parsing, the recommendation engine, and the tool-calling
loop into one conversational turn.

Each message is first classified (intent_parser.py). Small talk gets a
short fixed reply about what the assistant does, and off-topic messages get
OFF_TOPIC_REPLY; neither reaches the engine or the LLM's own answers, and
neither changes the conversation state.

Before the first build, a request that doesn't say what the PC is for
gets one question about its use (and budget, if missing). If the next
message still doesn't say, the build is sized for general use.

Without a budget, the first turn quotes the cheapest build that fully
covers the request (starting_budget.py) and asks for a budget. If the next
message still has none ("not sure", "you pick"), that build is shown.

Each session keeps its last few messages and the parts follow-up answers
named, so "switch to it" can pin the suggested part (pinned_parts); the
engine then re-runs around it like any other changed requirement.

Otherwise a turn takes one of three paths:

1. Build request with no build yet, or with unchanged requirements: runs parse_message -> RecommendationEngine.recommend
   -> explain_build. Build generation is never left to the model's choice
   of tool, so the engine cannot be bypassed by the model answering from
   its own knowledge.
2. Changed requirement with a build already shown ("what if I add
   10,000?", "make it 60k", "switch to that CPU"): the engine re-runs under
   the new constraints and the LLM explains the diff against the previous
   build.
3. Exploratory follow-up (comparisons, "why not X", spec lookups): handled
   by the tool-calling loop, where the model picks which lookup answers
   the question.

In every path the engine selects the parts and the LLM only narrates.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.llm.explain import explain_build, explain_followup
from app.llm.intent_parser import parse_message
from app.llm.providers.base import LLMProvider
from app.llm.tool_loop import run_tool_loop
from app.llm.tools import TOOL_DEFINITIONS, ToolExecutor
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
_USE_CASE_QUESTION = (
    "What will you mostly use the PC for? For example gaming (and at what resolution), "
    "video editing, 3D rendering, programming, or office work."
)
_BUDGET_TOO_QUESTION = " And do you have a budget in pesos? If you're not sure yet, that's fine."
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
    "prioritize_upgradeability", "existing_parts", "pinned_parts",
)

_BUILD_ROLES = ["cpu", "gpu", "motherboard", "ram", "storage", "psu", "cooler", "case"]

# Follow-ups can't run the engine themselves: a build made there would be
# described in the reply but never shown or kept. Changed requirements go
# through intent parsing instead, which re-runs the engine.
_FOLLOWUP_TOOLS = [t for t in TOOL_DEFINITIONS if t["function"]["name"] != "generate_build"]


@dataclass
class ChatTurnResult:
    reply: str
    state: BuildRequest
    build: BuildResult | None


def _engine_relevant_state_changed(old: BuildRequest | None, new: BuildRequest) -> bool:
    if old is None:
        return False
    return any(getattr(old, field) != getattr(new, field) for field in _ENGINE_RELEVANT_FIELDS)


def _part_name(part: dict) -> str:
    return f"{part['manufacturer']} {part['model']}"


def _build_parts(result: BuildResult | None) -> list[dict]:
    if result is None:
        return []
    components = result.primary.components
    parts = []
    for role in _BUILD_ROLES:
        value = getattr(components, role)
        for part in (value if isinstance(value, list) else [value] if value else []):
            parts.append({"id": str(part["id"]), "category": role, "name": _part_name(part),
                          "price_php": part.get("price_php")})
    return parts


def _pinnable_parts(session: ChatSession) -> list[dict]:
    """Parts the user can ask to pin: the ones follow-up answers named,
    newest first, then the current build's (for "keep this CPU")."""
    in_build = _build_parts(session.last_result)
    build_ids = {p["id"] for p in in_build}
    suggested = [{**p, "where": "suggested earlier"} for p in session.mentioned_parts if p["id"] not in build_ids]
    return suggested + [{**p, "where": "in the current build"} for p in in_build]


def _checked_pins(state: BuildRequest, previous: BuildRequest | None, pinnable: list[dict]) -> BuildRequest:
    """Drops pins whose ID the parser wasn't offered for that category, so
    an invented or misfiled ID can't reach the engine. Pins kept from the
    previous turn stay valid."""
    allowed = {(p["category"], p["id"]) for p in pinnable if p.get("price_php") is not None}
    if previous:
        allowed |= {(role, str(pid)) for role, pid in previous.pinned_parts.items()}
    pins = {role: pid for role, pid in state.pinned_parts.items() if (role, str(pid)) in allowed}
    if pins == state.pinned_parts:
        return state
    return state.model_copy(update={"pinned_parts": pins})


_HYPHENS = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"})


def _comparable(text: str) -> str:
    """Lowercase, with Unicode spaces and hyphens made plain. Models often
    write "Ryzen\u202f5\u202f7600" or "i5\u201112400F" for the catalog's plain names."""
    return re.sub(r"\s+", " ", text.translate(_HYPHENS)).lower()


def _parts_named_in(reply: str, executor: ToolExecutor, exclude: set[str]) -> list[dict]:
    """Priced parts the tool loop looked up and the reply names by model, so
    a later "switch to it" knows which product was meant."""
    text = _comparable(reply)
    named = []
    for part_id, part in executor.seen_parts.items():
        if part_id in exclude or part.get("price_php") is None or not part.get("model"):
            continue
        # Stop at the model's end so "Ryzen 5 7600" doesn't match "Ryzen 5 7600X".
        match = re.search(re.escape(_comparable(part["model"])) + r"(?![\w-])", text)
        if match:
            named.append((match.start(), {"id": part_id, "category": part["category"],
                                          "name": _part_name(part), "price_php": part["price_php"]}))
    # In the order the reply names them; the first is usually the suggestion.
    return [part for _, part in sorted(named, key=lambda item: item[0])]


def _new_pins(previous: BuildRequest | None, state: BuildRequest) -> dict:
    before = previous.pinned_parts if previous else {}
    return {role: pid for role, pid in state.pinned_parts.items() if before.get(role) != pid}


def _pin_only_change(previous: BuildRequest | None, state: BuildRequest) -> bool:
    if previous is None or not _new_pins(previous, state):
        return False
    unpinned = state.model_copy(update={"pinned_parts": previous.pinned_parts})
    return not _engine_relevant_state_changed(previous, unpinned)


def _only_repins_current_parts(previous: BuildRequest | None, state: BuildRequest, result: BuildResult) -> bool:
    """True when the only change is pinning parts the build already has,
    which happens when "switch to it" can't be matched to a suggestion."""
    if not _pin_only_change(previous, state):
        return False
    in_build = {(p["category"], p["id"]) for p in _build_parts(result)}
    return all((role, str(pid)) in in_build for role, pid in _new_pins(previous, state).items())


def _swap_in_pins(db: Session, state: BuildRequest, result: BuildResult) -> BuildResult | None:
    """The current build with only the newly pinned parts changed, when the
    rest of it still fits them and the total stays on budget."""
    keep: dict[str, uuid.UUID] = {}
    for part in _build_parts(result):
        keep.setdefault(part["category"], uuid.UUID(part["id"]))
    swapped = state.model_copy(update={"pinned_parts": {**keep, **state.pinned_parts}})
    swap = RecommendationEngine.recommend(db, swapped, explore=False)
    if swap is None or swap.primary.score.total_price_php > state.budget_php:
        return None
    return swap


def _unspent_note(result: BuildResult, state: BuildRequest) -> str:
    """Says why a build leaves much of the budget unspent, so a ₱69,500
    answer to a ₱600,000 request doesn't look like a misread budget."""
    if result.unspent_reason is None:
        return ""
    total = result.primary.score.total_price_php
    lead = (f"\n\nThis build comes to ₱{total:,.0f}, leaving ₱{state.budget_php - total:,.0f} "
            f"of your ₱{state.budget_php:,.0f} budget unspent: ")
    if result.unspent_reason == "top_tier":
        return lead + ("its CPU and GPU are already in the top performance tier, so spending more "
                       "wouldn't buy a faster tier. You could put the rest toward more storage or memory.")
    return lead + "the next step up in performance costs more than your budget."


def _already_in_build_reply(previous: BuildRequest | None, state: BuildRequest, result: BuildResult) -> str:
    names = {p["id"]: p["name"] for p in _build_parts(result)}
    parts = " and ".join(f"the {names[str(pid)]}" for pid in _new_pins(previous, state).values())
    return (f"Your build already has {parts}, so nothing changes. Which part did you want instead? "
            "You can also ask me for options, like \"is there a cheaper CPU?\"")


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
    """Runs one chat turn and records it in the session's history."""
    result, kind = _run_turn(db, llm, session, message)
    # Small talk and off-topic messages carry nothing later turns refer to.
    if kind not in (MessageKind.small_talk, MessageKind.off_topic):
        session.remember(message, result.reply)
    return result


def _run_turn(db: Session, llm: LLMProvider, session: ChatSession, message: str) -> tuple[ChatTurnResult, MessageKind]:
    previous_state = session.state
    awaiting_budget = previous_state is not None and previous_state.budget_php < MIN_VALID_BUDGET
    pinnable = _pinnable_parts(session)
    parsed = parse_message(
        llm, message, existing=session.state,
        has_build=session.last_result is not None, awaiting_budget=awaiting_budget,
        history=session.history, pinnable=pinnable,
    )
    kind = parsed.kind

    # Neither path touches session.state, so a stray message can't change
    # the build or count as the "still no budget" turn. No build is attached,
    # so the UI doesn't repeat the parts table under the reply.
    if kind is MessageKind.off_topic:
        return ChatTurnResult(reply=OFF_TOPIC_REPLY, state=previous_state or parsed.state, build=None), kind
    if kind is MessageKind.small_talk:
        reply = _FOLLOWUP_PROMPT_REPLY if session.last_result else _GREETING_REPLY
        return ChatTurnResult(reply=reply, state=previous_state or parsed.state, build=None), kind

    state = _checked_pins(parsed.state, previous_state, pinnable)
    session.state = state

    if not state.use_case and session.last_result is None and not session.asked_use_case:
        session.asked_use_case = True
        reply = _USE_CASE_QUESTION
        if state.budget_php < MIN_VALID_BUDGET:
            # This already asks for a budget, so a budget-less answer gets
            # the starting build rather than a second budget question.
            session.offered_starting_budget = True
            reply += _BUDGET_TOO_QUESTION
        return ChatTurnResult(reply=reply, state=state, build=None), kind

    if state.budget_php < MIN_VALID_BUDGET:
        starting = find_starting_budget(db, state)
        if starting is None:
            return ChatTurnResult(reply=_ask_for_budget(llm, state), state=state, build=session.last_result), kind
        if not session.offered_starting_budget:
            session.offered_starting_budget = True
            return ChatTurnResult(reply=_offer_starting_budget(state, starting), state=state, build=session.last_result), kind
        return _build_at_starting_budget(llm, session, state, starting), kind

    if session.last_result is not None and _only_repins_current_parts(previous_state, state, session.last_result):
        session.state = previous_state
        return ChatTurnResult(reply=_already_in_build_reply(previous_state, state, session.last_result),
                              state=previous_state, build=session.last_result), kind

    if session.last_result is not None and _engine_relevant_state_changed(previous_state, state):
        # A changed requirement with a build on screen, whether asked as a
        # what-if or as a request: re-run the engine and explain the diff,
        # so totals in the reply come from the engine, not the model.
        previous_result = session.last_result
        new_result = None
        if _pin_only_change(previous_state, state):
            # "Switch to it" changes just that part when the rest still fits;
            # otherwise the build is re-picked around it.
            new_result = _swap_in_pins(db, state, previous_result)
        if new_result is None:
            new_result = RecommendationEngine.recommend(db, state)
        if new_result is None:
            session.state = previous_state  # keep the last workable requirements
            reply = (
                "That change doesn't leave a compatible build under the other constraints; "
                "try a smaller change or adjust a different preference."
            )
            return ChatTurnResult(reply=reply, state=previous_state, build=previous_result), kind

        session.last_result = new_result
        reply = explain_followup(
            llm, message, previous_result, previous_state,
            comparison=new_result, comparison_request=state,
        )
        return ChatTurnResult(reply=reply + _unspent_note(new_result, state), state=state, build=new_result), kind

    if kind is MessageKind.build_request or session.last_result is None:
        result = RecommendationEngine.recommend(db, state)
        session.last_result = result
        if result is None:
            reply = (
                "I couldn't find a compatible build under these constraints. "
                "Try raising the budget or relaxing a preference (brand, Wi-Fi, form factor)."
            )
            return ChatTurnResult(reply=reply, state=state, build=None), kind
        reply = explain_build(llm, result, state) + _unspent_note(result, state)
        return ChatTurnResult(reply=reply, state=state, build=result), kind

    # Follow-up / exploratory question against the existing build: let the
    # model decide which tool(s) answer it, grounded in real data.
    executor = ToolExecutor(db)
    in_build = _build_parts(session.last_result)
    context = {
        "current_request": state.model_dump(mode="json"),
        "current_build_summary": {
            "primary_price_php": session.last_result.primary.score.total_price_php,
            # IDs let the model call get_part_details on the parts it's asked about.
            "primary_parts": {p["category"]: {"id": p["id"], "name": p["name"]} for p in in_build},
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are a PC-building assistant answering a follow-up question. "
                "Use the provided tools to look up any fact you need. Never state a spec, price, or "
                "compatibility result you haven't retrieved via a tool call. "
                "When you suggest a different part, name it with its catalog name and don't work out a new "
                "build total yourself; tell the user they can ask to switch to it, and the build will be "
                "recalculated. "
                "If the question isn't about PC hardware or this build, don't answer it; reply only: "
                f"\"{OFF_TOPIC_REPLY}\" "
                f"Context: {context}"
            ),
        },
        *session.history,
        {"role": "user", "content": message},
    ]
    reply = run_tool_loop(llm, executor, messages, tools=_FOLLOWUP_TOOLS)
    session.remember_parts(_parts_named_in(reply, executor, exclude={p["id"] for p in in_build}))
    return ChatTurnResult(reply=reply, state=state, build=session.last_result), kind
