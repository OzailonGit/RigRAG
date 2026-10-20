"""Retrieval evaluation set for the full catalog.

Each case pairs a query with a relevance rule over the part's structured
specs ("an AM5 board with Wi-Fi"), written by hand from what the query
asks for. Rules rather than expected model names, because on a catalog of
thousands of parts many results are equally right; a rule can judge any of
them, so precision@k means something. Every rule was checked by hand
against the results it accepted and rejected on the full catalog
(2026-10-20), and adjusted where it disagreed with a reasonable reading of
the query: "Mini-ITX case" no longer accepts Micro-ATX cases, and "best
gaming CPU" accepts the i9-14900K.

Rules only use fields the catalog has. A query whose intent the data can't
express (noise, looks) isn't in the set, since nothing could judge it. A
part missing the field a rule needs (an SSD with no listed read speed)
counts as not relevant: the catalog can't show that it fits.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class EvalCase:
    query: str
    category: str | None
    relevant: Callable[[dict], bool]
    rule: str  # the relevance rule in words, printed with the results
    max_price_php: float | None = None
    manufacturer: str | None = None
    k: int = 5


def _measured(part: dict) -> bool:
    return not part.get("benchmark_estimated")


EVAL_CASES: list[EvalCase] = [
    # --- GPUs ---
    EvalCase("good 1440p GPU with lots of VRAM", "gpu",
             lambda p: p["vram_gb"] >= 12 and _measured(p) and p["raster_1440p"] >= 40,
             "12GB+ VRAM and a measured 1440p score of at least 40% (high tier)"),
    EvalCase("budget graphics card under 20000 pesos", "gpu",
             lambda p: _measured(p) and p["raster_1440p"] >= 25,
             "worth buying for games: a measured 1440p score of at least 25% (mid tier)",
             max_price_php=20000),
    EvalCase("graphics card for 4K gaming", "gpu",
             lambda p: _measured(p) and p["raster_4k"] >= 50,
             "a measured 4K score of at least 50% of the RTX 5090"),
    EvalCase("NVIDIA RTX graphics card for ray tracing", "gpu",
             lambda p: (p.get("chip_vendor") or "").upper() == "NVIDIA" and "RTX" in (p.get("chip") or ""),
             "an NVIDIA GeForce RTX chip"),
    EvalCase("short graphics card that fits a small case", "gpu",
             lambda p: p["length_mm"] <= 210,
             "210mm long or shorter"),
    EvalCase("power efficient graphics card", "gpu",
             lambda p: p["tdp_w"] <= 150,
             "150W TDP or less"),
    EvalCase("ASUS graphics card under 30000", "gpu",
             lambda p: p["manufacturer"].lower() == "asus" and float(p["price_php"]) <= 30000,
             "made by ASUS and priced at ₱30,000 or less (checks the metadata filters)",
             max_price_php=30000, manufacturer="ASUS"),
    # --- CPUs ---
    EvalCase("quiet efficient CPU for a small form factor build", "cpu",
             lambda p: p["tdp_w"] <= 65,
             "65W TDP or less"),
    EvalCase("best CPU for gaming", "cpu",
             lambda p: _measured(p) and (p.get("gaming_score") or 0) >= 75,
             "a measured gaming score of at least 75% (about the top 20 CPUs tested)"),
    EvalCase("CPU with lots of cores for video rendering", "cpu",
             lambda p: p["cores"] >= 12,
             "12 cores or more"),
    EvalCase("processor with integrated graphics for a PC without a graphics card", "cpu",
             lambda p: bool(p["has_igpu"]),
             "has integrated graphics"),
    EvalCase("Intel CPU for an LGA1700 motherboard", "cpu",
             lambda p: p["socket"] == "LGA1700",
             "LGA1700 socket"),
    # --- Motherboards ---
    EvalCase("motherboard with WiFi for AM5 processors", "motherboard",
             lambda p: p["socket"] == "AM5" and bool(p["wifi"]),
             "AM5 socket and built-in Wi-Fi"),
    EvalCase("Mini-ITX motherboard", "motherboard",
             lambda p: p["form_factor"] == "Mini-ITX",
             "Mini-ITX form factor"),
    EvalCase("DDR4 motherboard for Intel 12th gen", "motherboard",
             lambda p: p["socket"] == "LGA1700" and p["ram_type"] == "DDR4",
             "LGA1700 socket and DDR4 memory"),
    # --- Memory ---
    EvalCase("32GB DDR5 memory kit", "ram",
             lambda p: p["ddr_gen"] == "DDR5" and p["capacity_gb"] == 32,
             "DDR5 and 32GB total"),
    EvalCase("fast DDR5-6000 CL30 RAM", "ram",
             lambda p: p["ddr_gen"] == "DDR5" and p["speed_mhz"] >= 6000 and (p.get("cas_latency") or 99) <= 30,
             "DDR5 at 6000MHz or faster with CL30 or lower"),
    # --- Storage ---
    EvalCase("fast NVMe SSD for gaming", "storage",
             lambda p: p["type"] == "SSD_NVME" and (p.get("read_mb_s") or 0) >= 5000,
             "NVMe with a rated read speed of 5000MB/s or more"),
    EvalCase("2TB SSD", "storage",
             lambda p: p["type"] != "HDD" and p["capacity_gb"] == 2000,
             "an SSD of 2TB"),
    # --- Power supplies ---
    EvalCase("power supply with enough headroom for a high-end GPU", "psu",
             lambda p: p["wattage"] >= 850,
             "850W or more"),
    EvalCase("750W 80+ Gold power supply", "psu",
             lambda p: p["wattage"] == 750 and p.get("efficiency_rating") == "80+ Gold",
             "750W and 80+ Gold"),
    # --- Coolers ---
    EvalCase("360mm AIO liquid cooler", "cooler",
             lambda p: p["type"] == "aio" and p.get("radiator_mm") == 360,
             "an AIO with a 360mm radiator"),
    EvalCase("low profile CPU cooler for a small case", "cooler",
             lambda p: p["type"] == "air" and (p.get("height_mm") or 999) <= 70,
             "an air cooler 70mm tall or less"),
    # --- Cases ---
    EvalCase("case with room for a long graphics card", "case",
             lambda p: (p.get("max_gpu_length_mm") or 0) >= 380,
             "a listed GPU clearance of 380mm or more"),
    EvalCase("small Mini-ITX case", "case",
             lambda p: p["form_factor_support"] == ["Mini-ITX"],
             "takes Mini-ITX boards only (a Micro-ATX case isn't a Mini-ITX case)"),
]
