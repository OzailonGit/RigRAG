import pytest

from app.llm.rule_based_intent import classify_message_rule_based, parse_intent_rule_based
from app.schemas import MessageKind, Resolution, UseCase


def test_extracts_budget_with_peso_sign():
    state = parse_intent_rule_based("Build me a ₱70,000 gaming PC")
    assert state.budget_php == 70000


def test_extracts_budget_with_k_suffix():
    state = parse_intent_rule_based("I have a 60k budget for gaming")
    assert state.budget_php == 60000


def test_extracts_resolution_and_use_case():
    state = parse_intent_rule_based("70000 pesos for 1440p gaming")
    assert state.budget_php == 70000
    assert state.resolution == Resolution.r1440p
    assert UseCase.gaming in state.use_case


def test_extracts_cpu_and_gpu_brand_preference():
    state = parse_intent_rule_based("50000 budget, want an AMD Ryzen CPU and an NVIDIA GPU")
    assert state.cpu_brand == "AMD"
    assert state.gpu_brand == "NVIDIA"


def test_extracts_wifi_requirement():
    state = parse_intent_rule_based("40000 pesos, need wifi built in")
    assert state.wifi_required is True


def test_raises_when_no_budget_and_no_prior_state():
    with pytest.raises(ValueError):
        parse_intent_rule_based("I want a gaming PC")


def test_merges_onto_existing_state_without_losing_fields():
    first = parse_intent_rule_based("70000 pesos for 1440p gaming, AMD CPU")
    second = parse_intent_rule_based("also need wifi", existing=first)

    assert second.budget_php == 70000  # preserved
    assert second.resolution == Resolution.r1440p  # preserved
    assert second.cpu_brand == "AMD"  # preserved
    assert second.wifi_required is True  # newly added


def test_updates_budget_on_existing_state():
    first = parse_intent_rule_based("50000 pesos gaming pc")
    second = parse_intent_rule_based("actually make it 80000", existing=first)
    assert second.budget_php == 80000


def test_relative_budget_increase_adds_to_existing_not_replaces():
    # "increase by X" is a delta, not a new absolute budget.
    first = parse_intent_rule_based("70000 pesos for gaming")
    second = parse_intent_rule_based("what if I increase my budget by 10000", existing=first)
    assert second.budget_php == 80000


def test_relative_budget_increase_with_k_suffix():
    first = parse_intent_rule_based("70000 pesos for gaming")
    second = parse_intent_rule_based("add 5k more to my budget", existing=first)
    assert second.budget_php == 75000


def test_relative_budget_decrease():
    first = parse_intent_rule_based("70000 pesos for gaming")
    second = parse_intent_rule_based("lower my budget by 10000", existing=first)
    assert second.budget_php == 60000


def test_trailing_more_phrasing_without_verb():
    first = parse_intent_rule_based("70000 pesos for gaming")
    second = parse_intent_rule_based("10000 more please", existing=first)
    assert second.budget_php == 80000


# --- Message classification without the LLM ---

@pytest.mark.parametrize("message,has_build,awaiting,kind", [
    ("hi", False, False, MessageKind.small_talk),
    ("Thanks for the build!", True, False, MessageKind.small_talk),
    ("what can you do?", False, False, MessageKind.small_talk),
    ("Recommend a movie for tonight", False, False, MessageKind.off_topic),
    ("Build me a website", True, False, MessageKind.off_topic),
    ("Tell me a joke", True, False, MessageKind.off_topic),
    ("What year did WW2 end, 1945?", False, False, MessageKind.off_topic),
    ("I'm not sure yet, what can you recommend?", False, True, MessageKind.build_request),
    ("I'm not sure", False, False, MessageKind.off_topic),
    ("70k gaming PC for 1440p", False, False, MessageKind.build_request),
    ("Build me something for 50k", False, False, MessageKind.build_request),
    ("Why this GPU?", True, False, MessageKind.followup),
    ("Is there anything cheaper?", True, False, MessageKind.followup),
    ("What if I increase my budget by 30000?", True, False, MessageKind.followup),
    ("Recommend a new build with an Intel CPU", True, False, MessageKind.build_request),
    ("Is there a lower budget CPU you can recommend?", True, False, MessageKind.followup),
    ("Can you suggest a quieter cooler?", True, False, MessageKind.followup),
    ("Recommend a cheaper CPU", False, False, MessageKind.build_request),
])
def test_classifies_message_kind(message, has_build, awaiting, kind):
    assert classify_message_rule_based(message, has_build, awaiting) is kind
