"""Finds a starting budget for users who haven't picked one.

Runs the engine at increasing budgets and returns the first whose
recommended build fully meets the CPU and GPU tiers the request needs (see
scoring/tiers.py). The figure quoted to the user is the price of a real
build from the current catalog, not an estimate.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.recommendation.engine import BuildResult, RecommendationEngine
from app.schemas import BuildRequest

BUDGET_LADDER_PHP = (
    [float(b) for b in range(25_000, 100_001, 5_000)]
    + [float(b) for b in range(110_000, 200_001, 10_000)]
    + [250_000.0, 300_000.0]
)
FULL_FIT = 0.999


@dataclass
class StartingBudget:
    budget_php: float
    result: BuildResult

    @property
    def price_php(self) -> float:
        return self.result.primary.score.total_price_php


def find_starting_budget(db: Session, request: BuildRequest) -> StartingBudget | None:
    """Lowest ladder budget whose build fully fits the request, or the
    best-fitting build on the ladder if none does. None only when the
    catalog can't produce any build for the request."""
    best: StartingBudget | None = None
    for budget in BUDGET_LADDER_PHP:
        # The quick search (no tier step-up, no alternatives) is enough to
        # find the budget; the full search runs once, on the one chosen.
        result = RecommendationEngine.recommend(db, request.model_copy(update={"budget_php": budget}), explore=False)
        # The engine falls back to the least-over-budget build when nothing
        # fits, which isn't a build at this budget.
        if result is None or result.primary.score.total_price_php > budget:
            continue
        candidate = StartingBudget(budget, result)
        if result.primary.score.performance_fit >= FULL_FIT:
            best = candidate
            break
        if best is None or result.primary.score.performance_fit > best.result.primary.score.performance_fit:
            best = candidate
    if best is None:
        return None
    full = RecommendationEngine.recommend(db, request.model_copy(update={"budget_php": best.budget_php}))
    return StartingBudget(best.budget_php, full) if full else best
