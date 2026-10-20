"""Tests chat orchestration with a fake LLMProvider.

Covers the branching logic (ask for budget vs. build vs. what-if vs.
tool-loop follow-up) and confirms the tool loop executes against real
data, without depending on real model latency or output variance.
"""

import json
from decimal import Decimal

import pytest

from app.database.session import SessionLocal
from app.llm.chat_orchestrator import handle_chat_message
from app.llm.providers.base import LLMProvider, LLMResponse, ToolCall
from app.models.product import Product
from app.schemas import BuildRequest
from app.services.chat_sessions import ChatSession


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    if session.query(Product).count() == 0:
        pytest.skip("Seed data not loaded. Run `python -m app.ingestion.seed` first.")
    yield session
    session.close()


class FakeLLMProvider(LLMProvider):
    """Returns canned responses based on what kind of call is being made,
    so orchestration logic can be tested without a real model."""

    def __init__(self, intent_json: dict | None = None, explanation: str = "Fake explanation.",
                 tool_call_sequence: list[list[ToolCall]] | None = None,
                 tool_answer: str = "Final tool-loop answer."):
        self.intent_json = intent_json
        self.explanation = explanation
        self.tool_answer = tool_answer
        self.tool_call_sequence = tool_call_sequence or []
        self._tool_call_index = 0
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, json_mode=False) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "json_mode": json_mode})

        if json_mode:
            return LLMResponse(content=json.dumps(self.intent_json))

        if tools:
            if self._tool_call_index < len(self.tool_call_sequence):
                calls = self.tool_call_sequence[self._tool_call_index]
                self._tool_call_index += 1
                return LLMResponse(content=None, tool_calls=calls)
            return LLMResponse(content=self.tool_answer)

        return LLMResponse(content=self.explanation)


def test_asks_for_budget_when_none_given(db):
    llm = FakeLLMProvider(intent_json={
        "budget_php": 1, "currency": "PHP", "use_case": ["gaming"], "resolution": None,
        "target_fps": None, "games": [], "cpu_brand": None, "gpu_brand": None,
        "wifi_required": False, "small_form_factor": False, "quiet": False,
        "low_power": False, "prioritize_upgradeability": False, "existing_parts": {},
    }, explanation="What's your budget in PHP?")
    session = ChatSession()

    result = handle_chat_message(db, llm, session, "I want a gaming PC")

    assert result.build is None
    assert "budget" in result.reply.lower()
    # The question comes with a real price to anchor on, not a bare "what's your budget?".
    assert "₱" in result.reply


def test_builds_starting_point_when_user_still_has_no_budget(db):
    no_budget = {
        "budget_php": 1, "currency": "PHP", "use_case": ["gaming"], "resolution": "1080p",
        "target_fps": None, "games": [], "cpu_brand": None, "gpu_brand": None,
        "wifi_required": True, "small_form_factor": False, "quiet": False,
        "low_power": False, "prioritize_upgradeability": False, "existing_parts": {},
    }
    llm = FakeLLMProvider(intent_json=no_budget, explanation="Here is the build.")
    session = ChatSession()

    first = handle_chat_message(db, llm, session, "Gaming PC for 1080p, I need Wi-Fi")
    second = handle_chat_message(db, llm, session, "I'm not sure yet, what can you recommend?")

    assert first.build is None and "1080p gaming with Wi-Fi" in first.reply
    assert second.build is not None
    assert second.build.primary.components.motherboard["wifi"] is True
    assert second.state.budget_php >= second.build.primary.score.total_price_php
    assert second.reply.startswith("Since you don't have a budget yet")


def test_build_request_runs_engine_and_returns_grounded_explanation(db):
    llm = FakeLLMProvider(
        intent_json={
            "budget_php": 70000, "currency": "PHP", "use_case": ["gaming"], "resolution": "1440p",
            "target_fps": None, "games": [], "cpu_brand": "AMD", "gpu_brand": None,
            "wifi_required": True, "small_form_factor": False, "quiet": False,
            "low_power": False, "prioritize_upgradeability": False, "existing_parts": {},
        },
        explanation="This build fits your budget.",
    )
    session = ChatSession()

    result = handle_chat_message(db, llm, session, "Build me a 70000 peso gaming PC, 1440p, AMD, need wifi")

    assert result.build is not None
    assert result.build.primary.score.total_price_php <= 70000
    assert result.reply == "This build fits your budget."
    assert session.last_result is result.build


def test_followup_question_uses_tool_loop_against_real_data(db):
    # First turn: establish a build.
    build_llm = FakeLLMProvider(
        intent_json={
            "budget_php": 70000, "currency": "PHP", "use_case": ["gaming"], "resolution": None,
            "target_fps": None, "games": [], "cpu_brand": None, "gpu_brand": None,
            "wifi_required": False, "small_form_factor": False, "quiet": False,
            "low_power": False, "prioritize_upgradeability": False, "existing_parts": {},
        },
        explanation="Here is your build.",
    )
    session = ChatSession()
    first = handle_chat_message(db, build_llm, session, "Build me a 70000 peso gaming PC")
    assert first.build is not None

    cpu_id = str(first.build.primary.components.cpu["id"])

    # Second turn: a follow-up question that triggers the tool loop.
    followup_llm = FakeLLMProvider(
        intent_json={
            "budget_php": 70000, "currency": "PHP", "use_case": ["gaming"], "resolution": None,
            "target_fps": None, "games": [], "cpu_brand": None, "gpu_brand": None,
            "wifi_required": False, "small_form_factor": False, "quiet": False,
            "low_power": False, "prioritize_upgradeability": False, "existing_parts": {},
        },
        tool_call_sequence=[
            [ToolCall(id="call_0", name="get_part_details", arguments={"product_id": cpu_id})],
        ],
    )
    second = handle_chat_message(db, followup_llm, session, "What's the socket on that CPU?")

    assert second.reply == "Final tool-loop answer."
    # Confirm the tool loop actually called chat with tools at least once.
    assert any(c["tools"] for c in followup_llm.calls)


def test_budget_whatif_reruns_engine_and_explains_diff(db):
    # First turn: establish a build at 60000.
    build_llm = FakeLLMProvider(
        intent_json={
            "budget_php": 60000, "currency": "PHP", "use_case": ["gaming"], "resolution": None,
            "target_fps": None, "games": [], "cpu_brand": None, "gpu_brand": None,
            "wifi_required": False, "small_form_factor": False, "quiet": False,
            "low_power": False, "prioritize_upgradeability": False, "existing_parts": {},
        },
        explanation="Here is your 60000 build.",
    )
    session = ChatSession()
    first = handle_chat_message(db, build_llm, session, "Build me a 60000 peso gaming PC")
    assert first.build is not None
    first_price = first.build.primary.score.total_price_php

    # Second turn: a what-if question that changes budget_php without using
    # a "build"/"recommend" keyword, so it must NOT go through the tool loop.
    whatif_llm = FakeLLMProvider(
        intent_json={
            "budget_php": 90000, "currency": "PHP", "use_case": ["gaming"], "resolution": None,
            "target_fps": None, "games": [], "cpu_brand": None, "gpu_brand": None,
            "wifi_required": False, "small_form_factor": False, "quiet": False,
            "low_power": False, "prioritize_upgradeability": False, "existing_parts": {},
        },
        explanation="With 90000 you'd get a better GPU.",
    )
    second = handle_chat_message(db, whatif_llm, session, "What if I increase my budget by 30000?")

    assert second.build is not None
    assert second.reply == "With 90000 you'd get a better GPU."
    assert second.state.budget_php == 90000
    # The engine actually re-ran under the new budget (not just re-explained
    # the old build), so the new build should reflect the higher budget.
    assert second.build.primary.score.total_price_php != first_price
    # Confirm no tool-calling loop was used for this turn.
    assert all(not c["tools"] for c in whatif_llm.calls)
    # Session state now reflects the new build going forward.
    assert session.last_result is second.build


def test_plain_lookup_question_does_not_trigger_engine_rerun(db):
    # A question that mentions no engine-relevant field changes should use
    # the tool loop, not silently re-run the engine.
    build_llm = FakeLLMProvider(
        intent_json={
            "budget_php": 60000, "currency": "PHP", "use_case": ["gaming"], "resolution": None,
            "target_fps": None, "games": [], "cpu_brand": None, "gpu_brand": None,
            "wifi_required": False, "small_form_factor": False, "quiet": False,
            "low_power": False, "prioritize_upgradeability": False, "existing_parts": {},
        },
        explanation="Here is your build.",
    )
    session = ChatSession()
    handle_chat_message(db, build_llm, session, "Build me a 60000 peso gaming PC")

    lookup_llm = FakeLLMProvider(
        intent_json={
            "budget_php": 60000, "currency": "PHP", "use_case": ["gaming"], "resolution": None,
            "target_fps": None, "games": [], "cpu_brand": None, "gpu_brand": None,
            "wifi_required": False, "small_form_factor": False, "quiet": False,
            "low_power": False, "prioritize_upgradeability": False, "existing_parts": {},
        },
        tool_call_sequence=[],
    )
    result = handle_chat_message(db, lookup_llm, session, "What socket does that CPU use?")

    assert result.reply == "Final tool-loop answer."
    assert any(c["tools"] for c in lookup_llm.calls)


# --- Message kinds: small talk and off-topic never reach the engine ---

def _state(budget: float, **overrides) -> dict:
    return {
        "budget_php": budget, "currency": "PHP", "use_case": ["gaming"], "resolution": None,
        "target_fps": None, "games": [], "cpu_brand": None, "gpu_brand": None,
        "wifi_required": False, "small_form_factor": False, "quiet": False,
        "low_power": False, "prioritize_upgradeability": False, "existing_parts": {}, **overrides,
    }


class UnreachableLLMProvider(LLMProvider):
    def chat(self, messages, tools=None, json_mode=False) -> LLMResponse:
        raise ConnectionError("provider is down")


def _session_with_build(db) -> ChatSession:
    session = ChatSession()
    handle_chat_message(db, FakeLLMProvider(intent_json=_state(70000)), session, "Build me a 70k gaming PC")
    assert session.last_result is not None
    return session


def test_off_topic_message_gets_fixed_reply_and_changes_nothing(db):
    from app.llm.chat_orchestrator import OFF_TOPIC_REPLY

    session = _session_with_build(db)
    state_before, build_before = session.state, session.last_result
    # The parser misreads a budget out of the message; an off-topic kind must still win.
    llm = FakeLLMProvider(intent_json={**_state(5), "message_kind": "off_topic"})

    result = handle_chat_message(db, llm, session, "Recommend a movie under 5 bucks")

    assert result.reply == OFF_TOPIC_REPLY
    assert result.build is None
    assert session.state is state_before and session.last_result is build_before
    assert len(llm.calls) == 1 and llm.calls[0]["json_mode"]  # no model-written answer


def test_small_talk_greets_without_quoting_a_budget(db):
    session = ChatSession()
    llm = FakeLLMProvider(intent_json={**_state(1), "message_kind": "small_talk"})

    result = handle_chat_message(db, llm, session, "hi")

    assert "budget" in result.reply and "₱" not in result.reply
    assert result.build is None
    assert session.offered_starting_budget is False
    # The next message without a budget still gets the starting-budget offer, not a build.
    llm.intent_json = _state(1, resolution="1080p")
    offer = handle_chat_message(db, llm, session, "gaming PC for 1080p")
    assert offer.build is None and "₱" in offer.reply


def test_greeting_works_when_the_llm_is_down(db):
    result = handle_chat_message(db, UnreachableLLMProvider(), ChatSession(), "hello")
    assert "budget" in result.reply


def test_build_request_kind_from_llm_reruns_engine_without_keywords(db):
    session = _session_with_build(db)
    llm = FakeLLMProvider(intent_json={**_state(60000), "message_kind": "build_request"},
                          explanation="Here is the 60k build.")

    result = handle_chat_message(db, llm, session, "Make it 60k")

    assert result.reply == "Here is the 60k build."
    assert result.build.primary.score.total_price_php <= 60000


def test_followup_prompt_tells_the_model_to_decline_unrelated_questions(db):
    from app.llm.chat_orchestrator import OFF_TOPIC_REPLY

    session = _session_with_build(db)
    llm = FakeLLMProvider(intent_json={**_state(70000), "message_kind": "followup"})

    handle_chat_message(db, llm, session, "Why this GPU?")

    tool_loop_system = next(c for c in llm.calls if c["tools"])["messages"][0]["content"]
    assert OFF_TOPIC_REPLY in tool_loop_system


def test_small_talk_kind_is_kept_when_llm_omits_the_state(db):
    llm = FakeLLMProvider(intent_json={"message_kind": "small_talk"})
    result = handle_chat_message(db, llm, ChatSession(), "hey there, how are you doing today")
    assert result.build is None and "budget" in result.reply


# --- Use case: asked, not assumed ---

def test_asks_what_the_pc_is_for_instead_of_assuming_gaming(db):
    session = ChatSession()
    llm = FakeLLMProvider(intent_json={**_state(1, use_case=[]), "message_kind": "build_request"},
                          explanation="Here is the build.")

    question = handle_chat_message(db, llm, session, "I want to build a PC but I'm unsure what to get")

    assert question.build is None
    assert "use the PC for" in question.reply and "budget" in question.reply
    assert "₱" not in question.reply  # no gaming build quoted

    # The answer gives a use case but still no budget: that question was
    # already asked, so the starting build comes straight away.
    llm.intent_json = {**_state(1, use_case=["video_editing"]), "message_kind": "build_request"}
    build = handle_chat_message(db, llm, session, "Video editing, I don't have a budget yet")

    assert build.build is not None
    assert build.reply.startswith("Since you don't have a budget yet") and "video editing" in build.reply


def test_use_case_question_is_asked_only_once(db):
    session = ChatSession()
    llm = FakeLLMProvider(intent_json={**_state(50000, use_case=[]), "message_kind": "build_request"})

    first = handle_chat_message(db, llm, session, "I want a PC for 50k")
    second = handle_chat_message(db, llm, session, "Not sure, just something decent")

    assert first.build is None and "use the PC for" in first.reply
    assert second.build is not None  # sized for general use
    assert second.state.use_case == []


# --- Memory and pinned parts ---

def _other_cpu_for(db, build) -> dict:
    """A priced CPU, not in the build, that fits the build's motherboard."""
    from app.models.product import Category
    from app.services import parts_service

    current = build.primary.components.cpu
    for product in db.query(Product).filter(Product.category == Category.cpu, Product.price_php.is_not(None)):
        part = parts_service.get_part(db, product.id)
        if part["socket"] == current["socket"] and part["id"] != current["id"]:
            return part
    pytest.skip("No other CPU on the same socket in this catalog")


def test_switch_to_a_suggested_part_pins_it_and_reruns_the_engine(db):
    session = _session_with_build(db)
    first_build = session.last_result
    other = _other_cpu_for(db, first_build)
    cpu_id = str(other["id"])

    suggest = FakeLLMProvider(
        intent_json={**_state(70000), "message_kind": "followup"},
        tool_call_sequence=[[ToolCall(id="call_0", name="get_part_details", arguments={"product_id": cpu_id})]],
        # Written the way models often write names: narrow no-break spaces, non-breaking hyphens.
        tool_answer=f"The {other['manufacturer']} {other['model']} would also work.".replace(" ", "\u202f").replace("-", "\u2011"),
    )
    handle_chat_message(db, suggest, session, "Is there a lower budget CPU you can recommend?")
    assert session.mentioned_parts[0]["id"] == cpu_id

    switch = FakeLLMProvider(
        intent_json={**_state(70000, pinned_parts={"cpu": cpu_id}), "message_kind": "build_request"},
        explanation="Switched the CPU.",
    )
    result = handle_chat_message(db, switch, session, "Switch to it")

    # The parser saw the earlier exchange and the suggested part's ID.
    parser_input = switch.calls[0]["messages"][1]["content"]
    assert "Is there a lower budget CPU" in parser_input and cpu_id in parser_input
    # The engine re-ran around the pinned part, and the reply is the diff explanation.
    assert str(result.build.primary.components.cpu["id"]) == cpu_id
    assert result.reply == "Switched the CPU."
    assert session.last_result is result.build and result.build is not first_build
    assert all(not c["tools"] for c in switch.calls)


def test_pin_with_an_id_the_parser_was_not_offered_is_dropped(db):
    import uuid

    session = _session_with_build(db)
    cpu_before = session.last_result.primary.components.cpu["id"]
    llm = FakeLLMProvider(
        intent_json={**_state(70000, pinned_parts={"cpu": str(uuid.uuid4())}), "message_kind": "build_request"},
    )

    result = handle_chat_message(db, llm, session, "Use the Ryzen 9 9950X")

    assert result.state.pinned_parts == {}
    assert result.build.primary.components.cpu["id"] == cpu_before


def test_followup_sees_recent_messages(db):
    session = _session_with_build(db)
    llm = FakeLLMProvider(intent_json={**_state(70000), "message_kind": "followup"})

    handle_chat_message(db, llm, session, "Why that one?")

    tool_messages = next(c for c in llm.calls if c["tools"])["messages"]
    assert {"role": "user", "content": "Build me a 70k gaming PC"} in tool_messages
    assert tool_messages[-1] == {"role": "user", "content": "Why that one?"}


def test_off_topic_messages_stay_out_of_the_history(db):
    session = _session_with_build(db)
    history_before = list(session.history)

    handle_chat_message(db, FakeLLMProvider(intent_json={**_state(70000), "message_kind": "off_topic"}),
                        session, "Recommend a movie")

    assert session.history == history_before


def test_prices_reach_the_model_with_the_peso_sign_unescaped(db):
    # "₱2,200" (the ASCII-escaped "₱2,200") reads as ₱12,200 to a model.
    llm = FakeLLMProvider(intent_json={**_state(70000), "message_kind": "build_request"})
    handle_chat_message(db, llm, ChatSession(), "Build me a 70k gaming PC")

    explanation_input = next(c for c in llm.calls if not c["json_mode"] and not c["tools"])["messages"][1]["content"]
    assert "₱" in explanation_input and "\\u20b1" not in explanation_input


def test_pinning_a_part_already_in_the_build_says_so_without_rerunning(db):
    session = _session_with_build(db)
    state_before, build_before = session.state, session.last_result
    cpu = build_before.primary.components.cpu
    llm = FakeLLMProvider(intent_json={**_state(70000, pinned_parts={"cpu": str(cpu["id"])}),
                                       "message_kind": "build_request"})

    result = handle_chat_message(db, llm, session, "Switch to it")

    assert cpu["model"] in result.reply and "already" in result.reply
    assert session.state is state_before and session.last_result is build_before
    assert len(llm.calls) == 1  # just the parser


def test_tool_loop_answers_on_its_last_round_instead_of_giving_up(db):
    from app.llm.tool_loop import MAX_ITERATIONS

    session = _session_with_build(db)
    cpu_id = str(session.last_result.primary.components.cpu["id"])
    lookup = [ToolCall(id="call", name="get_part_details", arguments={"product_id": cpu_id})]
    llm = FakeLLMProvider(intent_json={**_state(70000), "message_kind": "followup"},
                          tool_call_sequence=[lookup] * MAX_ITERATIONS, explanation="Answer from lookups.")

    result = handle_chat_message(db, llm, session, "Tell me everything about that CPU")

    assert result.reply == "Answer from lookups."
    assert not llm.calls[-1]["tools"]



def test_switching_to_a_part_that_fits_keeps_the_rest_of_the_build(db):
    from app.models.product import Category
    from app.services import parts_service

    session = _session_with_build(db)
    before = session.last_result.primary.components
    # Same socket, no hotter, and still on budget: a swap the rest of the build fits.
    room = 70000 - session.last_result.primary.score.total_price_php
    cheaper = None
    for product in db.query(Product).filter(Product.category == Category.cpu, Product.price_php.is_not(None)):
        part = parts_service.get_part(db, product.id)
        if (part["socket"] == before.cpu["socket"] and part["id"] != before.cpu["id"]
                and part["price_php"] <= before.cpu["price_php"] + Decimal(str(room))
                and part["tdp_w"] <= before.cpu["tdp_w"]):
            cheaper = part
            break
    if cheaper is None:
        pytest.skip("No other CPU on the same socket fits the rest of the build")
    session.remember_parts([{"id": str(cheaper["id"]), "category": "cpu", "name": cheaper["model"], "price_php": float(cheaper["price_php"])}])

    llm = FakeLLMProvider(intent_json={**_state(70000, pinned_parts={"cpu": str(cheaper["id"])}), "message_kind": "build_request"})
    after = handle_chat_message(db, llm, session, "Switch to it").build.primary.components

    assert after.cpu["id"] == cheaper["id"]
    for role in ("gpu", "motherboard", "ram", "psu", "cooler", "case"):
        assert getattr(after, role)["id"] == getattr(before, role)["id"], role


def test_followups_cannot_run_the_engine_themselves(db):
    session = _session_with_build(db)
    llm = FakeLLMProvider(intent_json={**_state(70000), "message_kind": "followup"})

    handle_chat_message(db, llm, session, "What would a 100k build look like?")

    offered = {t["function"]["name"] for c in llm.calls if c["tools"] for t in c["tools"]}
    assert offered and "generate_build" not in offered


def test_reply_explains_unspent_budget():
    from app.llm.chat_orchestrator import _unspent_note
    from app.recommendation.engine import BuildResult

    result = BuildResult.model_construct(
        primary=type("P", (), {"score": type("S", (), {"total_price_php": 204940.0})()})(),
        alternatives=[], unspent_reason="top_tier",
    )
    note = _unspent_note(result, BuildRequest(budget_php=600000))
    assert "₱204,940" in note and "₱395,060" in note and "top performance tier" in note
    assert _unspent_note(result.model_copy(update={"unspent_reason": None}), BuildRequest(budget_php=600000)) == ""


def test_session_survives_a_save_and_reload(db):
    from app.models.chat_session import ChatSessionRecord
    from app.services.chat_sessions import get_session, save_session

    session = _session_with_build(db)
    session.remember_parts([{"id": "x", "category": "cpu", "name": "AMD Ryzen 5 7600", "price_php": 11500.0}])
    try:
        save_session(db, "test-roundtrip", session)
        loaded = get_session(db, "test-roundtrip")
    finally:
        db.query(ChatSessionRecord).filter_by(id="test-roundtrip").delete()
        db.commit()

    assert loaded.state == session.state
    assert loaded.history == session.history and loaded.mentioned_parts == session.mentioned_parts
    assert loaded.last_result.primary.score == session.last_result.primary.score
    # Prices come back numeric, so explanations can format them.
    assert isinstance(loaded.last_result.primary.components.cpu["price_php"], float)
    assert get_session(db, "test-roundtrip").state is None  # deleted: a fresh session



def test_favored_build_is_explained_against_the_balanced_one(db):
    from app.recommendation.engine import RecommendationEngine

    llm = FakeLLMProvider(intent_json={**_state(50000, favor="cpu"), "message_kind": "build_request"})
    result = handle_chat_message(db, llm, ChatSession(), "What's the fastest CPU I can get in a 50k gaming PC?")

    explanation_input = json.loads(next(c for c in llm.calls if not c["json_mode"] and not c["tools"])["messages"][1]["content"])
    assert explanation_input["user_request"]["favor"] == "cpu"
    # The comparison is attached exactly when favoring the CPU changed the build.
    balanced = RecommendationEngine.recommend(db, result.state.model_copy(update={"favor": None}), explore=False)
    differs = balanced.primary.components.cpu["id"] != result.build.primary.components.cpu["id"]
    assert ("balanced_build" in explanation_input) == differs
