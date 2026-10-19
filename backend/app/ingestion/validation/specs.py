"""Validation for imported spec rows before they reach the database.

Required fields are taken from the spec model's NOT NULL columns, so this
stays in sync with the schema. Range checks reject values that are
physically implausible for consumer parts, which usually indicates a unit
or parsing error in the source datasheet.
"""

import enum

from sqlalchemy import inspect

from app.models.product import Category
from app.services.parts_service import SPEC_MODEL_BY_CATEGORY

# (min, max) inclusive, per category and field.
RANGES: dict[Category, dict[str, tuple[float, float]]] = {
    Category.gpu: {"vram_gb": (1, 64), "tdp_w": (30, 700), "recommended_psu_w": (300, 1600), "length_mm": (100, 420)},
    Category.motherboard: {"max_ram_gb": (16, 512), "ram_slots": (1, 8), "m2_slots": (0, 8), "sata_ports": (0, 12)},
    Category.ram: {"capacity_gb": (2, 256), "sticks": (1, 8), "speed_mhz": (1600, 12000), "cas_latency": (8, 60), "voltage": (1.0, 1.65)},
    Category.storage: {"capacity_gb": (64, 32000), "read_mb_s": (50, 16000), "write_mb_s": (50, 16000)},
    Category.psu: {"wattage": (200, 2000)},
    Category.cooler: {"height_mm": (30, 200), "radiator_mm": (120, 420), "tdp_rating_w": (40, 400)},
    Category.case: {"max_gpu_length_mm": (150, 500), "max_cooler_height_mm": (40, 250)},
}

# Fields a compatibility rule reads even though the column is nullable.
# Case clearances and cooler sockets may be unknown: the rules warn about
# those instead of checking them (see compatibility/rules.py).
EXTRA_REQUIRED: dict[Category, set[str]] = {
    Category.case: {"form_factor_support"},
}


def _enum_values(column) -> set[str] | None:
    enum_class = getattr(column.type, "enum_class", None)
    if enum_class is None or not issubclass(enum_class, enum.Enum):
        return None
    return {member.value for member in enum_class}


def validate_spec(category: Category, spec: dict) -> list[str]:
    """Returns a list of problems; an empty list means the row can be stored."""
    problems = []
    model = SPEC_MODEL_BY_CATEGORY[category]

    for attr in inspect(model).column_attrs:
        if attr.key == "product_id":
            continue
        column = attr.columns[0]
        value = spec.get(attr.key)
        if value is None:
            if not column.nullable and column.default is None:
                problems.append(f"missing {attr.key}")
            continue
        allowed = _enum_values(column)
        if allowed is not None and value not in allowed:
            problems.append(f"{attr.key}={value!r} is not one of {sorted(allowed)}")

    for key in EXTRA_REQUIRED.get(category, set()):
        if not spec.get(key):
            problems.append(f"missing {key}")

    for key, (low, high) in RANGES.get(category, {}).items():
        value = spec.get(key)
        if value is not None and not (low <= value <= high):
            problems.append(f"{key}={value} outside plausible range {low}-{high}")

    return problems
