"""Generates candidate full builds via incremental beam search.

An exhaustive cross-product of 6 candidates across 8 categories is 6^8
(~1.7M) combinations. Beam search expands one role at a time, prunes
incompatible branches immediately using the rules whose inputs are already
present, and keeps the best BEAM_WIDTH partial builds before expanding the
next role, so cost grows linearly with roles x candidates x beam width.
"""

from app.recommendation.compatibility import rules
from app.recommendation.compatibility.build_components import BuildComponents
from app.recommendation.optimization.budget_allocator import allocate_budget
from app.recommendation.optimization.candidate_generation import fetch_candidates
from app.recommendation.scoring.tiers import needed_cpu_tier, needed_gpu_tier, performance_fit
from app.schemas import BuildRequest
from app.services import parts_service

ROLE_ORDER = ["cpu", "motherboard", "ram", "gpu", "case", "cooler", "storage", "psu"]
BEAM_WIDTH = 10
PRICE_DEVIATION_WEIGHT = 2.0


def _ready_compatibility_results(partial: dict) -> list:
    present = set(partial.keys())
    results = []
    if {"cpu", "motherboard"} <= present:
        results.append(rules.check_cpu_motherboard(partial["cpu"], partial["motherboard"]))
    if {"motherboard", "ram"} <= present:
        results.append(rules.check_motherboard_ram(partial["motherboard"], partial["ram"]))
    if {"motherboard", "case"} <= present:
        results.append(rules.check_motherboard_case(partial["motherboard"], partial["case"]))
    if "case" in present:
        results.append(rules.check_gpu_case(partial.get("gpu"), partial["case"]))
    if {"cooler", "cpu"} <= present:
        results.append(rules.check_cooler_cpu(partial["cooler"], partial["cpu"]))
    if {"cooler", "case"} <= present:
        results.append(rules.check_cooler_case(partial["cooler"], partial["case"]))
    if {"storage", "motherboard"} <= present:
        results.append(rules.check_storage_motherboard(partial["storage"], partial["motherboard"]))
    if {"psu", "cpu"} <= present:
        results.append(rules.check_psu_system(partial["psu"], partial["cpu"], partial.get("gpu")))
    return results


def _has_blocking(results) -> bool:
    return any(not r.compatible and r.severity == "blocking" for r in results)


def _partial_heuristic(partial: dict, bands: dict[str, tuple[float, float]], request: BuildRequest) -> float:
    """Cheap proxy for ranking partial builds mid-search: how well CPU/GPU fit
    what the use case needs (see scoring/tiers.py), penalized by drift from
    cumulative target spend.

    Ranking by raw tier would bias the beam toward the priciest part in every
    category. Fit-to-need matches what final scoring rewards.
    """
    price = 0.0
    target = 0.0
    fits = []
    for role, value in partial.items():
        if role == "storage":
            price += sum(float(item["price_php"]) for item in value)
        else:
            price += float(value["price_php"])
            if role == "cpu":
                fits.append(performance_fit(value.get("performance_tier"), needed_cpu_tier(request)))
            elif role == "gpu":
                fits.append(performance_fit(value.get("performance_tier"), needed_gpu_tier(request)))
        if role in bands:
            target += sum(bands[role]) / 2

    fit_score = sum(fits) / len(fits) if fits else 0.5
    price_deviation = abs(price - target) / target if target else 0.0
    return fit_score - PRICE_DEVIATION_WEIGHT * price_deviation


def generate_builds(db, request: BuildRequest) -> list[BuildComponents]:
    bands = allocate_budget(request)
    owned = request.existing_parts

    def candidates_for(role: str) -> list:
        if role in owned:
            part = parts_service.get_part(db, owned[role])
            if part is None:
                return []
            return [[part] if role == "storage" else part]
        band = bands.get(role, (0.0, request.budget_php))
        candidates = fetch_candidates(db, role, band, request)
        return [[c] for c in candidates] if role == "storage" else candidates

    beam: list[dict] = [{}]
    for role in ROLE_ORDER:
        candidates = candidates_for(role)
        if not candidates:
            return []  # no viable part exists for this role under current constraints

        expanded = []
        for partial in beam:
            for candidate in candidates:
                merged = {**partial, role: candidate}
                if _has_blocking(_ready_compatibility_results(merged)):
                    continue
                expanded.append(merged)

        if not expanded:
            return []  # every candidate for this role conflicted with something already chosen

        expanded.sort(key=lambda m: _partial_heuristic(m, bands, request), reverse=True)
        beam = expanded[:BEAM_WIDTH]

    return [BuildComponents(**partial) for partial in beam]
