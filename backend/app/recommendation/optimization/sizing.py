"""Capacity ranges for parts whose tier doesn't capture how much is enough.

Without these, ranking by closeness to a budget band picks whatever capacity
the band can buy: a 96GB kit for a gaming PC, a 1650W PSU for a 300W system.
Ranges are heuristics, like the tiers in scoring/tiers.py.
"""

from app.schemas import BuildRequest, UseCase

HEAVY_USE_CASES = {UseCase.productivity, UseCase.video_editing, UseCase.rendering_3d}

# (min_gb, max_gb). A 480GB drive counts as "500GB".
_RAM_GB = {"default": (16, 32), "heavy": (32, 64), UseCase.ai_ml: (32, 128)}
_STORAGE_GB = {"default": (480, 2000), "heavy": (1000, 4000), UseCase.ai_ml: (1000, 4000)}

# A PSU this far above the estimated need adds cost without adding value.
PSU_MAX_OVER_REQUIRED = 2.0
PSU_MAX_FLOOR_W = 750


def _range(table: dict, request: BuildRequest) -> tuple[int, int]:
    ranges = [table[UseCase.ai_ml]] if UseCase.ai_ml in request.use_case else []
    if HEAVY_USE_CASES & set(request.use_case):
        ranges.append(table["heavy"])
    ranges = ranges or [table["default"]]
    return max(r[0] for r in ranges), max(r[1] for r in ranges)


def ram_capacity_range(request: BuildRequest) -> tuple[int, int]:
    return _range(_RAM_GB, request)


def storage_capacity_range(request: BuildRequest) -> tuple[int, int]:
    return _range(_STORAGE_GB, request)


def psu_wattage_range(required_w: int) -> tuple[int, int]:
    return required_w, max(round(required_w * PSU_MAX_OVER_REQUIRED), PSU_MAX_FLOOR_W)
