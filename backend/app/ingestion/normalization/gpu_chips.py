"""Chip-level GPU reference data (TDP, vendor, performance tier).

Icecat describes board-partner cards but not chip TDP, so those values come
from data/reference/gpu_chips.json, keyed by a normalized chip name.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REFERENCE_PATH = Path(__file__).resolve().parents[4] / "data" / "reference" / "gpu_chips.json"

_VENDOR_WORDS = re.compile(r"\b(nvidia|amd|intel|geforce|radeon)\b")


@dataclass(frozen=True)
class GpuChip:
    chip: str
    vendor: str
    tdp_w: int
    performance_tier: str


def chip_key(name: str) -> str:
    """Normalizes chip names so "NVIDIA GeForce RTX 4060 Ti" and "RTX4060TI"
    produce the same key ("rtx4060ti")."""
    lowered = _VENDOR_WORDS.sub(" ", name.lower())
    return re.sub(r"[^a-z0-9]", "", lowered)


@lru_cache
def load_chips(path: Path = REFERENCE_PATH) -> dict[str, GpuChip]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {chip_key(c["chip"]): GpuChip(**c) for c in raw["chips"]}


def lookup_chip(name: str | None) -> GpuChip | None:
    if not name:
        return None
    return load_chips().get(chip_key(name))
