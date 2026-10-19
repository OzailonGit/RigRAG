"""Ordinal mapping for performance_tier, plus heuristics for how much tier
a given use case and resolution need.

A heuristic, not a benchmark-derived model. It gives "don't just pick the
most expensive part" something concrete to measure: a build that heavily
over-provisions tier for its use case scores worse on fit than one that
matches it, even though it costs more.
"""

from app.schemas import BuildRequest, Resolution, UseCase

TIER_ORDINAL = {"entry": 1, "mid": 2, "high": 3, "enthusiast": 4}

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


def needed_gpu_tier(request: BuildRequest) -> int:
    if request.resolution is not None:
        return _GPU_TIER_BY_RESOLUTION[request.resolution]
    return 2 if UseCase.gaming in request.use_case else 3


def needed_cpu_tier(request: BuildRequest) -> int:
    return max((_CPU_TIER_BY_USE_CASE.get(uc, 2) for uc in request.use_case), default=2)


def performance_fit(actual_tier: str | None, needed_ordinal: int) -> float:
    """1.0 at an exact tier match, decaying for both under- and
    over-provisioning (an enthusiast GPU for a 1080p esports build wastes
    budget just as surely as an entry GPU underserves a 4K build)."""
    actual = tier_ordinal(actual_tier)
    return max(0.0, 1 - abs(actual - needed_ordinal) / 3)
