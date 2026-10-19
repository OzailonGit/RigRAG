"""Top-level recommendation engine: the only place that turns a
BuildRequest into a ranked, compatibility-checked build.

Contains no LLM calls. The API and LLM tool calls invoke it identically.
"""

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.recommendation.compatibility.build_components import BuildComponents
from app.recommendation.compatibility.checker import CompatibilityChecker
from app.recommendation.compatibility.results import CompatibilityResult
from app.recommendation.optimization.beam_search import generate_builds
from app.recommendation.optimization.budget_allocator import allocate_budget
from app.recommendation.scoring.scoring import ScoreBreakdown, score_build
from app.schemas import BuildRequest


class ScoredBuild(BaseModel):
    label: str
    components: BuildComponents
    score: ScoreBreakdown
    compatibility: list[CompatibilityResult]


class BuildResult(BaseModel):
    primary: ScoredBuild
    alternatives: list[ScoredBuild]


def _label_alternatives(primary: ScoredBuild, ranked: list[ScoredBuild]) -> list[ScoredBuild]:
    """Pick 2-3 alternates that differ meaningfully from the primary build
    (cheaper, higher performance, more upgradeable) instead of the next-highest
    scores, which tend to be one part swap away from the primary.
    """
    rest = [b for b in ranked if b is not primary]
    alternates: list[ScoredBuild] = []

    cheaper = [b for b in rest if b.score.total_price_php < primary.score.total_price_php]
    if cheaper:
        pick = min(cheaper, key=lambda b: b.score.total_price_php)
        alternates.append(pick.model_copy(update={"label": "Lower cost"}))

    higher_perf = [b for b in rest if b.score.performance_fit > primary.score.performance_fit]
    if higher_perf:
        pick = max(higher_perf, key=lambda b: b.score.performance_fit)
        if pick not in [a for a in alternates]:
            alternates.append(pick.model_copy(update={"label": "Higher performance"}))

    more_upgradeable = [b for b in rest if b.score.upgrade_headroom > primary.score.upgrade_headroom]
    if more_upgradeable:
        pick = max(more_upgradeable, key=lambda b: b.score.upgrade_headroom)
        alternates.append(pick.model_copy(update={"label": "More upgradeable"}))

    return alternates[:3]


class RecommendationEngine:
    @staticmethod
    def recommend(db: Session, request: BuildRequest) -> BuildResult | None:
        bands = allocate_budget(request)
        candidates = generate_builds(db, request)
        if not candidates:
            return None

        scored: list[ScoredBuild] = []
        for components in candidates:
            compatibility = CompatibilityChecker.run(components)
            if CompatibilityChecker.has_blocking_issue(compatibility):
                continue  # re-verify on the final build; never trust a stale check
            score = score_build(components, request, bands)
            scored.append(ScoredBuild(label="Recommended", components=components, score=score, compatibility=compatibility))

        if not scored:
            return None

        # Budget is a hard user constraint, not just one term in the score.
        # Prefer builds that fit it outright and fall back to the least-over-budget
        # option only when none do.
        in_budget = [b for b in scored if b.score.total_price_php <= request.budget_php]
        pool = in_budget if in_budget else scored

        pool.sort(key=lambda b: b.score.total, reverse=True)
        primary = pool[0]
        alternatives = _label_alternatives(primary, pool)

        return BuildResult(primary=primary, alternatives=alternatives)
