"""Measured speed of a CPU or GPU for a given request, from the relative
benchmark scores app.ingestion.import_benchmarks loads (0 to 1, where 1 is
the fastest part tested).

Tiers (tiers.py) say whether a part is enough for the request; speed says
how much faster one part is than another of the same tier, which is what
lets a bigger budget buy a faster card instead of the cheapest one that
clears the tier. A benchmarked part's tier comes from its score too
(speed_tier), so the two never disagree.
"""

from app.models.cpu import Cpu
from app.models.gpu import Gpu
from app.recommendation.scoring.tiers import TIER_ORDINAL, tier_ordinal
from app.schemas import BuildRequest, Resolution, UseCase

# Uses that load every core; anything else is scored on gaming speed.
MULTITHREADED_USES = {UseCase.productivity, UseCase.programming, UseCase.video_editing,
                      UseCase.rendering_3d, UseCase.ai_ml}

# Lowest score (% of the fastest part tested) for mid, high, and enthusiast.
# GPUs use the 1440p score whatever the resolution, so a card has one tier;
# the resolution sets which tier is needed. Roughly: mid holds 60fps at
# 1080p ultra, high at 1440p ultra, enthusiast at 4K ultra.
TIER_FLOORS = {
    "raster_1440p": (25, 40, 60),
    "gaming_score": (45, 65, 80),
    "multithread_score": (25, 45, 70),
}
_TIER_NAMES = ["entry", "mid", "high", "enthusiast"]

_GPU_COLUMN = {Resolution.r1080p: "raster_1080p", Resolution.r1440p: "raster_1440p", Resolution.r4k: "raster_4k"}


def _gpu_field(request: BuildRequest) -> str:
    # 1440p when no resolution is given: the middle ground most builds target.
    return _GPU_COLUMN.get(request.resolution, "raster_1440p")


def _cpu_fields(request: BuildRequest) -> list[str]:
    uses = set(request.use_case)
    if uses & MULTITHREADED_USES:
        return ["gaming_score", "multithread_score"] if UseCase.gaming in uses else ["multithread_score"]
    return ["gaming_score"]


def _from_tier(part: dict) -> float:
    """Stand-in before any benchmarks are imported."""
    return tier_ordinal(part.get("performance_tier")) / 5


def gpu_speed(gpu: dict | None, request: BuildRequest) -> float:
    if gpu is None:
        return 0.0
    score = gpu.get(_gpu_field(request))
    return score / 100 if score is not None else _from_tier(gpu)


def cpu_speed(cpu: dict, request: BuildRequest) -> float:
    scores = [cpu.get(name) for name in _cpu_fields(request)]
    scores = [s for s in scores if s is not None]
    return sum(scores) / len(scores) / 100 if scores else _from_tier(cpu)


def speed_column(category: str, request: BuildRequest):
    """The score column candidate generation compares parts on, or None for
    categories without benchmarks."""
    if category == "gpu":
        return getattr(Gpu, _gpu_field(request))
    if category == "cpu":
        fields = _cpu_fields(request)
        columns = [getattr(Cpu, name) for name in fields]
        return columns[0] if len(columns) == 1 else (columns[0] + columns[1]) / 2
    return None


def tier_for_score(score: float, field: str) -> str:
    floors = TIER_FLOORS[field]
    return _TIER_NAMES[sum(1 for floor in floors if score >= floor)]


def speed_tier(part: dict | None, category: str, request: BuildRequest) -> int | None:
    """Tier ordinal from the part's benchmark scores for this request, or
    None when it has none (the stored tier applies then)."""
    if part is None:
        return None
    if category == "gpu":
        score = part.get("raster_1440p")
        return TIER_ORDINAL[tier_for_score(score, "raster_1440p")] if score is not None else None
    fields = [f for f in _cpu_fields(request) if part.get(f) is not None]
    if not fields:
        return None
    return round(sum(TIER_ORDINAL[tier_for_score(part[f], f)] for f in fields) / len(fields))


def part_tier(part: dict | None, category: str, request: BuildRequest) -> int:
    tier = speed_tier(part, category, request)
    return tier if tier is not None else tier_ordinal(part.get("performance_tier") if part else None)


def tier_columns(category: str, request: BuildRequest) -> dict:
    """The score columns speed_tier reads, by field name, for candidate queries."""
    if category == "gpu":
        return {"raster_1440p": Gpu.raster_1440p}
    if category == "cpu":
        return {name: getattr(Cpu, name) for name in _cpu_fields(request)}
    return {}
