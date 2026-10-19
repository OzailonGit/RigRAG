"""Benchmark matching and the speed and tier rules built on it."""

import json

import pytest

from app.ingestion.import_benchmarks import REFERENCE, chip_key, cpu_key
from app.recommendation.scoring.performance import cpu_speed, gpu_speed, speed_tier, tier_for_score
from app.recommendation.scoring.tiers import ordinal_fit
from app.schemas import BuildRequest, Resolution, UseCase


@pytest.mark.parametrize("a,b", [
    ("GeForce RTX 4070 SUPER", "RTX 4070 Super"),
    ("Intel Arc B580", "Arc B580"),
    ("GeForce RTX 4060 Ti", "RTX 4060 Ti 16GB"),
])
def test_chip_names_from_every_source_match(a, b):
    assert chip_key(a) == chip_key(b)


@pytest.mark.parametrize("model,base", [
    ("Core i5-12400F", "core i5-12400"),
    ("Core i9-14900KF", "core i9-14900k"),
    ("Core Ultra 7 265KF", "core ultra 7 265k"),
    ("Core Ultra 5 225F", "core ultra 5 225"),
    # AMD F parts are different chips, not the same chip with graphics off.
    ("Ryzen 5 7500F", "ryzen 5 7500f"),
])
def test_intel_f_chips_share_the_non_f_score(model, base):
    assert cpu_key(model) == base


def test_reference_scores_are_sourced_and_in_range():
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert "tomshardware.com" in data["source"]
    for gpu in data["gpus"]:
        for field in ("raster_1080p", "raster_1440p", "raster_4k"):
            assert 0 < gpu[field] <= 100, gpu
    assert any(g["raster_1440p"] == 100 for g in data["gpus"])
    for cpu in data["cpus"]:
        assert cpu["gaming_1080p"] or cpu["multithread"], cpu
        for field in ("gaming_1080p", "multithread"):
            assert cpu[field] is None or 0 < cpu[field] <= 101, cpu


def test_gpu_speed_follows_the_requested_resolution():
    card = {"raster_1080p": 60.0, "raster_1440p": 50.0, "raster_4k": 40.0}
    assert gpu_speed(card, BuildRequest(budget_php=1, resolution=Resolution.r4k)) == pytest.approx(0.4)
    assert gpu_speed(card, BuildRequest(budget_php=1, resolution=Resolution.r1080p)) == pytest.approx(0.6)
    assert gpu_speed(card, BuildRequest(budget_php=1)) == pytest.approx(0.5)


def test_cpu_speed_and_tier_follow_the_use_case():
    x3d = {"gaming_score": 97.0, "multithread_score": 54.7}
    gaming = BuildRequest(budget_php=1, use_case=[UseCase.gaming])
    editing = BuildRequest(budget_php=1, use_case=[UseCase.video_editing])
    assert cpu_speed(x3d, gaming) == pytest.approx(0.97)
    assert cpu_speed(x3d, editing) == pytest.approx(0.547)
    assert speed_tier(x3d, "cpu", gaming) == 4  # enthusiast for games
    assert speed_tier(x3d, "cpu", editing) == 3  # high for editing


def test_unbenchmarked_parts_fall_back_to_their_tier():
    assert speed_tier({"performance_tier": "high"}, "gpu", BuildRequest(budget_php=1)) is None
    assert gpu_speed({"performance_tier": "high"}, BuildRequest(budget_php=1)) > 0


@pytest.mark.parametrize("score,tier", [(24.9, "entry"), (25, "mid"), (40, "high"), (64.6, "enthusiast")])
def test_gpu_tiers_from_1440p_score(score, tier):
    assert tier_for_score(score, "raster_1440p") == tier


def test_exceeding_the_needed_tier_is_not_penalized():
    assert ordinal_fit(4, 2) == 1.0
    assert ordinal_fit(2, 2) == 1.0
    assert ordinal_fit(1, 3) == pytest.approx(1 / 3)
