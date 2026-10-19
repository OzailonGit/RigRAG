"""Manual spec corrections from manufacturer pages (data/reference/spec_overrides.json).

Clearance figures are rounded up when entered, so a correction never makes
a part look smaller than the manufacturer states.
"""

import json
from functools import lru_cache
from pathlib import Path

OVERRIDES_PATH = Path(__file__).resolve().parents[4] / "data" / "reference" / "spec_overrides.json"


@lru_cache
def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {entry["mpn"].upper(): entry for entry in raw["overrides"]}


def apply_overrides(mpn: str | None, spec: dict) -> list[str]:
    """Applies any override for `mpn` to `spec` in place and returns the
    overridden field names."""
    if not mpn:
        return []
    entry = load_overrides().get(mpn.upper())
    if entry is None:
        return []
    spec.update(entry["spec"])
    return sorted(entry["spec"])
