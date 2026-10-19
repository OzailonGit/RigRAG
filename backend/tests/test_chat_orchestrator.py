"""Tests chat orchestration with a fake LLMProvider.

Covers the branching logic (ask for budget vs. build vs. what-if vs.
tool-loop follow-up) and confirms the tool loop executes against real
data, without depending on real model latency or output variance.
"""

import json

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
                 tool_call_sequence: list[list[ToolCall]] | None = None):
        self.intent_json = intent_json
        self.explanation = explanation
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
            return LLMResponse(content="Final tool-loop answer.")

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
