"""Ordinal mapping for performance_tier, plus heuristics for how much tier
a given use case and resolution need.

The needs are heuristics, not benchmark-derived. A part's own tier comes
from its benchmark scores when it has them (performance.py), so "is this
enough for 1440p?" is judged on measured speed.
"""

from app.schemas import BuildRequest, Resolution, UseCase

TIER_ORDINAL = {"entry": 1, "mid": 2, "high": 3, "enthusiast": 4}
MAX_TIER = 4

_GPU_TIER_BY_RESOLUTION = {
    Resolution.r1080p: 2,  # mid
    Resolution.r1440p: 3,  # high
    Resolution.r4k: 4,     # enthusiast
}

_CPU_TIER_BY_USE_CASE = {
    UseCase.gaming: 2,
    UseCase.general: 2,
    UseCase.mixed: 2,
    UseCase.productivity: 3,
    UseCase.programming: 3,
    UseCase.video_editing: 3,
    UseCase.rendering_3d: 3,
    UseCase.ai_ml: 3,
}


def tier_ordinal(performance_tier: str | None) -> int:
    return TIER_ORDINAL.get(performance_tier, 2)  # unknown tier treated as "mid"


def needed_gpu_tier(request: BuildRequest, boost: int = 0) -> int:
    """GPU tier the request needs, raised by `boost` tiers when the budget
    can pay for more (see RecommendationEngine.recommend)."""
    if request.resolution is not None:
        base = _GPU_TIER_BY_RESOLUTION[request.resolution]
    else:
        # An unstated use case gets the same mid-tier GPU as gaming rather
        # than the higher tier creative work needs.
        base = 2 if not request.use_case or UseCase.gaming in request.use_case else 3
    return min(base + boost, MAX_TIER)


def needed_cpu_tier(request: BuildRequest, boost: int = 0) -> int:
    base = max((_CPU_TIER_BY_USE_CASE.get(uc, 2) for uc in request.use_case), default=2)
    return min(base + boost, MAX_TIER)


def ordinal_fit(actual_ordinal: int, needed_ordinal: int) -> float:
    """1.0 once a part meets the needed tier, decaying for each tier short.
    Going past the need isn't penalized: the budget caps spending, and the
    value term (scoring.py) stops a pricier part winning for little gain."""
    return max(0.0, 1 - max(needed_ordinal - actual_ordinal, 0) / 3)
