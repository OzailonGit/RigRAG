import pytest

from app.llm.rule_based_intent import parse_intent_rule_based
from app.schemas import Resolution, UseCase


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
