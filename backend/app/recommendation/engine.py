"""Top-level recommendation engine: the only place that turns a
BuildRequest into a ranked, compatibility-checked build.

Contains no LLM calls. The API and LLM tool calls invoke it identically.
"""

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.recommendation.compatibility.build_components import BuildComponents
from app.recommendation.compatibility.checker import CompatibilityChecker
from app.recommendation.compatibility.results import CompatibilityResult
from app.recommendation.optimization.beam_search import generate_builds, load_fixed_parts
from app.recommendation.optimization.budget_allocator import allocate_budget
from app.recommendation.scoring.scoring import ScoreBreakdown, score_build
from app.recommendation.scoring.tiers import needed_cpu_tier, needed_gpu_tier
from app.schemas import BuildRequest


class ScoredBuild(BaseModel):
    label: str
    components: BuildComponents
    score: ScoreBreakdown
    compatibility: list[CompatibilityResult]


class BuildResult(BaseModel):
    primary: ScoredBuild
    alternatives: list[ScoredBuild]
    # Why the primary build leaves budget unspent, when it leaves a lot:
    # "top_tier" (nothing faster to aim for) or "next_tier_over_budget".
    unspent_reason: str | None = None


# A build under this share of the budget tries the next tier up.
SPEND_TARGET = 0.85
FULL_FIT = 0.999
# An alternative has to differ enough to be worth a tab.
LOWER_COST_BUDGET_SHARE = 0.90
MIN_SAVING_SHARE = 0.05
MIN_SAVING_PHP = 2_000
MAX_OVER_BUDGET_SHARE = 0.20
MIN_HEADROOM_GAIN = 0.15


def _warning_count(build: ScoredBuild) -> int:
    return sum(1 for r in build.compatibility if r.severity == "warning")


def _rank(db: Session, request: BuildRequest, boost: int) -> list[ScoredBuild] | None:
    """Every compatible candidate build at this tier boost, best first, with
    in-budget builds ahead of over-budget ones."""
    fixed = load_fixed_parts(db, request)
    if fixed is None:
        return None
    pinned_spend = sum(float(fixed[role]["price_php"]) for role in request.pinned_parts
                       if role not in request.existing_parts)
    bands = allocate_budget(request, pinned_spend)

    scored: list[ScoredBuild] = []
    for components in generate_builds(db, request, fixed, bands, boost):
        compatibility = CompatibilityChecker.run(components)
        if CompatibilityChecker.has_blocking_issue(compatibility):
            continue  # re-verify on the final build; never trust a stale check
        score = score_build(components, request, bands, compatibility, boost)
        scored.append(ScoredBuild(label="Recommended", components=components, score=score, compatibility=compatibility))
    if not scored:
        return None

    # Budget is a hard user constraint, not just one term in the score.
    # Prefer builds that fit it outright and fall back to the least-over-budget
    # option only when none do.
    in_budget = [b for b in scored if b.score.total_price_php <= request.budget_php]
    pool = in_budget if in_budget else scored
    return sorted(pool, key=lambda b: b.score.total, reverse=True)


def _can_step_up(request: BuildRequest, boost: int) -> bool:
    return (needed_cpu_tier(request, boost + 1) > needed_cpu_tier(request, boost)
            or needed_gpu_tier(request, boost + 1) > needed_gpu_tier(request, boost))


def _full_fit(ranked: list[ScoredBuild] | None, budget: float) -> ScoredBuild | None:
    """The best build that fully meets the (boosted) needs within `budget`."""
    for build in ranked or []:
        if build.score.performance_fit >= FULL_FIT and build.score.total_price_php <= budget:
            return build
    return None


def _lower_cost(db: Session, request: BuildRequest, primary: ScoredBuild, boost: int) -> ScoredBuild | None:
    """The best build on a smaller budget, if it saves a meaningful amount
    without adding compatibility warnings."""
    price = primary.score.total_price_php
    smaller = request.model_copy(update={"budget_php": price * LOWER_COST_BUDGET_SHARE})
    ranked = _rank(db, smaller, max(boost - 1, 0))
    if not ranked:
        return None
    pick = ranked[0]
    saving = price - pick.score.total_price_php
    if saving < max(MIN_SAVING_PHP, price * MIN_SAVING_SHARE) or _warning_count(pick) > _warning_count(primary):
        return None
    return pick.model_copy(update={"label": "Lower cost"})


def _alternatives(db: Session, request: BuildRequest, primary: ScoredBuild, pool: list[ScoredBuild],
                  boost: int, next_tier: ScoredBuild | None) -> list[ScoredBuild]:
    alternates = []
    cheaper = _lower_cost(db, request, primary, boost)
    if cheaper:
        alternates.append(cheaper)
    if next_tier and next_tier.score.total_price_php > primary.score.total_price_php:
        alternates.append(next_tier.model_copy(update={"label": "Higher performance"}))
    roomier = [b for b in pool if b.score.upgrade_headroom >= primary.score.upgrade_headroom + MIN_HEADROOM_GAIN]
    if roomier:
        pick = max(roomier, key=lambda b: b.score.upgrade_headroom)
        alternates.append(pick.model_copy(update={"label": "More upgradeable"}))
    return alternates


class RecommendationEngine:
    @staticmethod
    def recommend(db: Session, request: BuildRequest, explore: bool = True) -> BuildResult | None:
        """The best build for `request`, or None when nothing compatible exists.

        With `explore`, a build that leaves much of the budget unspent is
        retried one tier up (CPU and GPU) while the faster build still fits
        the budget, and alternatives are computed: a cheaper build, the next
        tier up when it's slightly over budget, and a more upgradeable one.
        Without it, only the cheapest build that meets the request's own
        needs comes back, as find_starting_budget wants.
        """
        boost = 0
        pool = _rank(db, request, boost)
        if pool is None:
            return None
        if not explore:
            return BuildResult(primary=pool[0], alternatives=[])

        # Try every higher tier, not just the next: a large budget can buy a
        # GPU past the request's need, which makes an in-between tier miss
        # its exact fit even though the tier above it fits.
        target = request.budget_php * SPEND_TARGET
        level = 0
        while pool[0].score.total_price_php < target and _can_step_up(request, level):
            level += 1
            ranked = _rank(db, request, level)
            pick = _full_fit(ranked, request.budget_php)
            if pick is not None:
                pool, boost = [pick, *(b for b in ranked if b is not pick)], level

        unspent_reason = None
        if pool[0].score.total_price_php < target:
            unspent_reason = "next_tier_over_budget" if _can_step_up(request, boost) else "top_tier"

        # The next tier up, priced with a little more budget, for the
        # "Higher performance" tab.
        next_tier = None
        if _can_step_up(request, boost):
            stretch = request.budget_php * (1 + MAX_OVER_BUDGET_SHARE)
            next_tier = _full_fit(_rank(db, request.model_copy(update={"budget_php": stretch}), boost + 1), stretch)

        primary = pool[0]
        return BuildResult(
            primary=primary,
            alternatives=_alternatives(db, request, primary, pool[1:], boost, next_tier),
            unspent_reason=unspent_reason,
        )
