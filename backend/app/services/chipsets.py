"""PCIe generations a motherboard chipset fixes, from
data/reference/chipsets.json (AMD chipsets only: on Intel boards the
answer depends on the board, so none is given)."""

import json
from functools import lru_cache
from pathlib import Path

REFERENCE = Path(__file__).resolve().parents[3] / "data" / "reference" / "chipsets.json"


@lru_cache
def _index() -> dict[tuple[str, str], dict]:
    entries = json.loads(REFERENCE.read_text(encoding="utf-8"))["chipsets"]
    return {(e["socket"], e["chipset"].upper()): e for e in entries}


def chipset_pcie(socket: str | None, chipset: str | None) -> dict | None:
    """{"graphics_pcie", "nvme_pcie", "source"} for the chipset, or None."""
    if not socket or not chipset:
        return None
    return _index().get((socket, chipset.upper()))
