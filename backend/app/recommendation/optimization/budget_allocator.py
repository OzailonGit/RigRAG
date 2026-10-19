"""Turns a total budget into soft per-category bands.

Bands are not hard caps: candidate_generation.py applies a flex margin so
a slightly pricier motherboard isn't excluded for missing its slice by a
few pesos. The final build-level filter in engine.py keeps the total on
budget.

The percentages are a hand-tuned starting heuristic per category role,
not measured from real build data.
"""

from app.schemas import BuildRequest, UseCase

CATEGORIES = ["cpu", "gpu", "motherboard", "ram", "storage", "psu", "cooler", "case"]

# Percentage of total budget, per use case. Gaming/AI skew GPU-heavy;
# programming/productivity skew CPU and RAM heavy.
TEMPLATES: dict[UseCase, dict[str, float]] = {
    UseCase.gaming: {
        "cpu": 0.20, "gpu": 0.35, "motherboard": 0.10, "ram": 0.08,
        "storage": 0.07, "psu": 0.08, "cooler": 0.05, "case": 0.07,
    },
    UseCase.productivity: {
        "cpu": 0.30, "gpu": 0.15, "motherboard": 0.12, "ram": 0.12,
        "storage": 0.10, "psu": 0.08, "cooler": 0.06, "case": 0.07,
    },
    UseCase.programming: {
        "cpu": 0.28, "gpu": 0.12, "motherboard": 0.12, "ram": 0.16,
        "storage": 0.12, "psu": 0.08, "cooler": 0.05, "case": 0.07,
    },
    UseCase.video_editing: {
        "cpu": 0.25, "gpu": 0.30, "motherboard": 0.10, "ram": 0.12,
        "storage": 0.08, "psu": 0.08, "cooler": 0.04, "case": 0.03,
    },
    UseCase.rendering_3d: {
        "cpu": 0.25, "gpu": 0.32, "motherboard": 0.09, "ram": 0.12,
        "storage": 0.07, "psu": 0.08, "cooler": 0.04, "case": 0.03,
    },
    UseCase.ai_ml: {
        "cpu": 0.15, "gpu": 0.45, "motherboard": 0.08, "ram": 0.12,
        "storage": 0.08, "psu": 0.07, "cooler": 0.03, "case": 0.02,
    },
    UseCase.general: {
        "cpu": 0.22, "gpu": 0.25, "motherboard": 0.12, "ram": 0.10,
        "storage": 0.08, "psu": 0.09, "cooler": 0.06, "case": 0.08,
    },
    UseCase.mixed: {
        "cpu": 0.24, "gpu": 0.28, "motherboard": 0.11, "ram": 0.10,
        "storage": 0.08, "psu": 0.08, "cooler": 0.05, "case": 0.06,
    },
}

BAND_FLEX = 0.35  # candidates within +/-35% of a category's soft allocation are considered


def _blended_template(use_cases: list[UseCase]) -> dict[str, float]:
    templates = [TEMPLATES[uc] for uc in use_cases if uc in TEMPLATES] or [TEMPLATES[UseCase.general]]
    blended = {cat: sum(t[cat] for t in templates) / len(templates) for cat in CATEGORIES}
    total = sum(blended.values())
    return {cat: pct / total for cat, pct in blended.items()}  # renormalize to 1.0


def allocate_budget(request: BuildRequest) -> dict[str, tuple[float, float]]:
    """Returns {category: (band_min_php, band_max_php)} for categories the
    user doesn't already own. Owned categories (request.existing_parts) get
    their allocation redistributed proportionally across the rest.
    """
    template = _blended_template(request.use_case)

    owned = set(request.existing_parts.keys())
    shopping_for = [c for c in CATEGORIES if c not in owned]
    reclaimed_pct = sum(template[c] for c in owned)

    shopping_total_pct = sum(template[c] for c in shopping_for) or 1.0
    bands = {}
    for cat in shopping_for:
        # redistribute the owned categories' share proportionally to what's left
        adjusted_pct = template[cat] + reclaimed_pct * (template[cat] / shopping_total_pct)
        target = request.budget_php * adjusted_pct
        bands[cat] = (target * (1 - BAND_FLEX), target * (1 + BAND_FLEX))

    return bands
