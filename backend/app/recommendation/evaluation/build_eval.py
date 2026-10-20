"""Runs the build evaluation set (build_cases.py) against the catalog.

    python -m app.recommendation.evaluation.build_eval
    python -m app.recommendation.evaluation.build_eval --max-seconds 2

Exits non-zero when any check fails, so it can gate a change. Times the
recommendation engine alone; replies through /api/chat add the language
model's time on top, which depends on the provider.
"""

import argparse
import sys
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.product import Category, Product
from app.recommendation.compatibility.checker import CompatibilityChecker
from app.recommendation.engine import MAX_OVER_BUDGET_SHARE, BuildResult, RecommendationEngine
from app.recommendation.evaluation.build_cases import BUILD_CASES, BuildCase
from app.recommendation.scoring.performance import part_tier

DEFAULT_MAX_SECONDS = 3.0


@dataclass
class CaseOutcome:
    case: BuildCase
    seconds: float
    failures: list[str]
    summary: str


def _request_for(db: Session, case: BuildCase):
    if case.owns:
        owned = (db.query(Product).filter(Product.category == Category(case.owns), Product.price_php.is_not(None))
                 .order_by(Product.id).first())
        return case.request.model_copy(update={"existing_parts": {case.owns: owned.id}})
    return case.request


def _core_failures(case: BuildCase, request, result: BuildResult | None) -> list[str]:
    """Compatibility and budget: what every answer must get right."""
    if not case.expect_build:
        return [] if result is None else ["returned a build for an impossible request"]
    if result is None:
        return ["returned no build"]
    failures = []
    for build in [result.primary, *result.alternatives]:
        if CompatibilityChecker.has_blocking_issue(build.compatibility):
            failures.append(f"{build.label} build has a blocking compatibility issue")
        limit = request.budget_php * (1 + MAX_OVER_BUDGET_SHARE) if build.label == "Higher performance" else request.budget_php
        if build.score.total_price_php > limit:
            failures.append(f"{build.label} build is over budget (₱{build.score.total_price_php:,.0f})")
    return failures


def run_case(db: Session, case: BuildCase, max_seconds: float) -> CaseOutcome:
    request = _request_for(db, case)
    started = time.perf_counter()
    result = RecommendationEngine.recommend(db, request)
    seconds = time.perf_counter() - started

    failures = _core_failures(case, request, result)
    if seconds > max_seconds:
        failures.append(f"took {seconds:.2f}s (limit {max_seconds:.1f}s)")
    summary = "no build"
    if result is not None and case.expect_build:
        parts = result.primary.components
        cpu_tier = part_tier(parts.cpu, "cpu", request)
        gpu_tier = part_tier(parts.gpu, "gpu", request) if parts.gpu else 0
        if cpu_tier < case.min_cpu_tier:
            failures.append(f"CPU tier {cpu_tier}, expected at least {case.min_cpu_tier}")
        if gpu_tier < case.min_gpu_tier:
            failures.append(f"GPU tier {gpu_tier}, expected at least {case.min_gpu_tier}")
        for description, check in case.checks:
            if not check(result):
                failures.append(f"expected {description}")
        gpu = parts.gpu["model"] if parts.gpu else "integrated graphics"
        summary = (f"₱{result.primary.score.total_price_php:,.0f}: {parts.cpu['model']} (tier {cpu_tier}) / "
                   f"{gpu} (tier {gpu_tier})")
    return CaseOutcome(case, seconds, failures, summary)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS, help="time limit per request")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        # One untimed run first, so connection setup isn't charged to the first case.
        RecommendationEngine.recommend(db, BUILD_CASES[0].request, explore=False)
        outcomes = [run_case(db, case, args.max_seconds) for case in BUILD_CASES]
    finally:
        db.close()

    for outcome in outcomes:
        status = "PASS" if not outcome.failures else "FAIL"
        print(f"[{status}] {outcome.case.name} ({outcome.seconds:.2f}s)  {outcome.summary}")
        for failure in outcome.failures:
            print(f"    - {failure}")
        if outcome.failures and outcome.case.note:
            print(f"      ({outcome.case.note})")

    times = sorted(o.seconds for o in outcomes)
    passed = sum(1 for o in outcomes if not o.failures)
    print(f"\n{passed}/{len(outcomes)} cases pass")
    print(f"Engine time: median {times[len(times) // 2]:.2f}s, slowest {times[-1]:.2f}s")
    sys.exit(0 if passed == len(outcomes) else 1)


if __name__ == "__main__":
    main()
