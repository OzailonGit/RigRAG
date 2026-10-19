"""Deterministic build scoring.

score = w_perf * performance_fit
      + w_speed * speed
      + w_value * price_to_performance
      + w_pref * preference_match
      + w_upgrade * upgrade_headroom
      - w_power * power_penalty
      - w_unverified * unverified_checks
      - budget_overrun_penalty

Weights are hand-tuned against the example requests in
tests/test_recommendation.py, not derived from data.
"""

from pydantic import BaseModel

from app.recommendation.compatibility.build_components import BuildComponents
from app.recommendation.compatibility.results import CompatibilityResult
from app.recommendation.compatibility.rules import is_unverified
from app.recommendation.compatibility.power import recommended_psu_watts
from app.recommendation.optimization.budget_allocator import blended_template
from app.recommendation.scoring.performance import cpu_speed, gpu_speed, part_tier
from app.recommendation.scoring.tiers import needed_cpu_tier, needed_gpu_tier, ordinal_fit, tier_ordinal
from app.schemas import BuildRequest

W_PERF = 0.35
# Measured speed on top of tier fit, so among builds that meet the need the
# faster one wins when the budget allows it.
W_SPEED = 0.25
W_VALUE = 0.25
W_PREF = 0.15
W_UPGRADE = 0.15
W_POWER = 0.10
# Per compatibility check that couldn't run for lack of specs. Large enough
# that a verified case or cooler wins unless the unverified one is much cheaper.
W_UNVERIFIED = 0.05
BUDGET_OVERRUN_SCALE = 2.0

SMALL_FORM_FACTORS = {"Micro-ATX", "Mini-ITX"}
MODERN_SOCKETS = {"AM5", "LGA1700", "LGA1851"}


class ScoreBreakdown(BaseModel):
    total: float
    performance_fit: float
    price_to_performance: float
    preference_match: float
    upgrade_headroom: float
    power_penalty: float
    unverified_penalty: float = 0.0
    speed: float = 0.0
    budget_overrun_penalty: float
    total_price_php: float


def _total_price(build: BuildComponents) -> float:
    """What the user pays: parts they already own (marked "owned" by
    beam_search.load_fixed_parts) cost nothing."""
    parts = [build.cpu, build.motherboard, build.ram, build.psu, build.cooler, build.case, *build.storage]
    if build.gpu:
        parts.append(build.gpu)
    return sum(float(p["price_php"]) for p in parts if not p.get("owned"))


# Benchmark speed at which a CPU or GPU counts as top-end for value: about
# where "enthusiast" parts sit relative to the fastest part tested.
TOP_END_SPEED = 0.8
VALUE_CAP = 2.0


def _price_to_performance(build: BuildComponents, bands: dict[str, tuple[float, float]],
                          request: BuildRequest) -> float:
    """Performance earned per peso spent, normalized against each category's
    target spend (the band midpoint) so cheap categories (RAM) don't
    structurally outscore expensive ones (GPU) just from having a smaller
    price. CPUs and GPUs count measured speed; other parts count tier."""
    scores = []
    for category, part in [
        ("cpu", build.cpu), ("gpu", build.gpu), ("motherboard", build.motherboard),
        ("ram", build.ram), ("psu", build.psu), ("cooler", build.cooler), ("case", build.case),
    ]:
        if part is None or category not in bands:
            continue
        band_mid = sum(bands[category]) / 2
        relative_price = max(float(part["price_php"]) / band_mid, 0.1) if band_mid else 1.0
        if category == "cpu":
            performance = min(cpu_speed(part, request) / TOP_END_SPEED, 1.0)
        elif category == "gpu":
            performance = min(gpu_speed(part, request) / TOP_END_SPEED, 1.0)
        else:
            performance = tier_ordinal(part.get("performance_tier")) / 4
        # Capped per part, at twice the value of a top-end part bought at
        # the target spend, so one very cheap part can't max out the term.
        scores.append(min(performance / relative_price, VALUE_CAP) / VALUE_CAP)
    return sum(scores) / len(scores) if scores else 0.0


def build_speed(cpu: dict | None, gpu: dict | None, request: BuildRequest, gpu_pending: bool = False) -> float:
    """CPU and GPU speed, weighted by how the use case splits its budget
    between them (a gaming build leans on the GPU, a workstation on the CPU).
    A build without a GPU is all CPU, unless the GPU just isn't chosen yet
    (`gpu_pending`, mid-search), when the CPU keeps only its share."""
    template = blended_template(request.use_case)
    cpu_share = template["cpu"] / (template["cpu"] + template["gpu"])
    cpu_part = cpu_speed(cpu, request) if cpu else 0.0
    if gpu is None:
        return cpu_share * cpu_part if gpu_pending else cpu_part
    return cpu_share * cpu_part + (1 - cpu_share) * gpu_speed(gpu, request)


def _preference_match(build: BuildComponents, request: BuildRequest) -> float:
    scores = []
    if request.small_form_factor:
        scores.append(1.0 if build.case["form_factor_support"][0] in SMALL_FORM_FACTORS or
                      any(f in SMALL_FORM_FACTORS for f in build.case["form_factor_support"]) else 0.3)
        scores.append(1.0 if build.motherboard["form_factor"] in SMALL_FORM_FACTORS else 0.3)
    if request.low_power:
        total_tdp = build.cpu["tdp_w"] + (build.gpu["tdp_w"] if build.gpu else 0)
        scores.append(max(0.0, 1 - total_tdp / 400))
    if request.wifi_required:
        scores.append(1.0 if build.motherboard.get("wifi") else 0.0)
    return sum(scores) / len(scores) if scores else 0.5  # neutral when no relevant preference was stated


def _upgrade_headroom(build: BuildComponents) -> float:
    ram_headroom = max(0.0, (build.motherboard["ram_slots"] - build.ram["sticks"]) / build.motherboard["ram_slots"])
    required_watts = recommended_psu_watts(build.cpu, build.gpu)
    psu_headroom = max(0.0, min((build.psu["wattage"] - required_watts) / build.psu["wattage"], 0.5)) / 0.5
    socket_longevity = 1.0 if build.cpu["socket"] in MODERN_SOCKETS else 0.5
    return (ram_headroom + psu_headroom + socket_longevity) / 3


def _power_penalty(build: BuildComponents, request: BuildRequest) -> float:
    total_tdp = build.cpu["tdp_w"] + (build.gpu["tdp_w"] if build.gpu else 0)
    penalty = min(total_tdp / 600, 1.0)
    return penalty * (2.0 if request.low_power else 1.0)


def _budget_overrun_penalty(total_price: float, budget: float) -> float:
    if total_price <= budget:
        return 0.0
    overrun_ratio = (total_price - budget) / budget
    return min(BUDGET_OVERRUN_SCALE * overrun_ratio**2, 5.0)


def score_build(
    build: BuildComponents,
    request: BuildRequest,
    bands: dict[str, tuple[float, float]],
    compatibility: list[CompatibilityResult] = (),
    boost: int = 0,
) -> ScoreBreakdown:
    cpu_fit = ordinal_fit(part_tier(build.cpu, "cpu", request), needed_cpu_tier(request, boost))
    gpu_fit = ordinal_fit(part_tier(build.gpu, "gpu", request), needed_gpu_tier(request, boost))
    perf_fit = (cpu_fit + gpu_fit) / 2 if build.gpu else cpu_fit

    value = _price_to_performance(build, bands, request)
    speed = build_speed(build.cpu, build.gpu, request)
    pref = _preference_match(build, request)
    upgrade = _upgrade_headroom(build)
    power_pen = _power_penalty(build, request)
    total_price = _total_price(build)
    overrun_pen = _budget_overrun_penalty(total_price, request.budget_php)
    unverified_pen = W_UNVERIFIED * sum(1 for r in compatibility if is_unverified(r))

    total = (
        W_PERF * perf_fit
        + W_SPEED * speed
        + W_VALUE * value
        + W_PREF * pref
        + W_UPGRADE * upgrade
        - W_POWER * power_pen
        - unverified_pen
        - overrun_pen
    )

    return ScoreBreakdown(
        total=total,
        performance_fit=perf_fit,
        price_to_performance=value,
        preference_match=pref,
        upgrade_headroom=upgrade,
        power_penalty=power_pen,
        unverified_penalty=unverified_pen,
        speed=speed,
        budget_overrun_penalty=overrun_pen,
        total_price_php=total_price,
    )
