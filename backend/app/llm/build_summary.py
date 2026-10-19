"""Compacts a BuildResult or ScoredBuild into the minimum an LLM needs to
explain it.

A raw BuildResult.model_dump() (every part's full DB row plus every
alternative's nested build) exceeds Groq's free-tier tokens-per-minute and
request-size (HTTP 413) limits, especially on the budget what-if flow,
which sends two builds in one call.

Each part is reduced to one sentence via build_product_document, which
already excludes noise fields like id and timestamps. "info"-severity
compatibility results are dropped since they only confirm that a rule
passed.
"""

from app.rag.chunking.document_builder import build_product_document
from app.recommendation.engine import BuildResult
from app.recommendation.engine import ScoredBuild


_OWNED_NOTE = " The user already owns this part, so its price isn't in the total."


def _describe(part: dict) -> str:
    return build_product_document(part) + (_OWNED_NOTE if part.get("owned") else "")


def summarize_scored_build(build: ScoredBuild) -> dict:
    parts = {}
    for role in ("cpu", "gpu", "motherboard", "ram", "psu", "cooler", "case"):
        part = getattr(build.components, role)
        if part:
            parts[role] = _describe(part)
    if build.components.storage:
        parts["storage"] = [_describe(s) for s in build.components.storage]

    notable_issues = [
        {"rule": r.rule, "severity": r.severity, "message": r.message}
        for r in build.compatibility
        if r.severity != "info"
    ]

    return {
        "label": build.label,
        "parts": parts,
        "total_price_php": build.score.total_price_php,
        "score_total": round(build.score.total, 3),
        "fully_compatible": not any(not r.compatible and r.severity == "blocking" for r in build.compatibility),
        "compatibility_notes": notable_issues,
    }


def summarize_build_result(result: BuildResult) -> dict:
    return {
        "primary": summarize_scored_build(result.primary),
        "alternatives": [summarize_scored_build(alt) for alt in result.alternatives],
    }
