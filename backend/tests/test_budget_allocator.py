import uuid

import pytest

from app.recommendation.optimization.budget_allocator import allocate_budget
from app.schemas import BuildRequest, UseCase


def _midpoints(bands):
    return {cat: sum(band) / 2 for cat, band in bands.items()}


def test_bands_split_the_whole_budget():
    bands = allocate_budget(BuildRequest(budget_php=70000, use_case=[UseCase.gaming]))
    assert sum(_midpoints(bands).values()) == pytest.approx(70000)


def test_owned_part_share_goes_to_the_other_categories():
    request = BuildRequest(budget_php=70000, use_case=[UseCase.gaming], existing_parts={"case": uuid.uuid4()})
    bands = allocate_budget(request)
    assert "case" not in bands
    assert sum(_midpoints(bands).values()) == pytest.approx(70000)


def test_pinned_part_price_comes_off_the_budget_first():
    request = BuildRequest(budget_php=70000, use_case=[UseCase.gaming], pinned_parts={"cpu": uuid.uuid4()})
    bands = allocate_budget(request, pinned_spend=12000)
    assert "cpu" not in bands
    assert sum(_midpoints(bands).values()) == pytest.approx(58000)


def test_unstated_use_case_is_sized_like_general_use():
    unstated = allocate_budget(BuildRequest(budget_php=70000))
    general = allocate_budget(BuildRequest(budget_php=70000, use_case=[UseCase.general]))
    assert unstated == general
