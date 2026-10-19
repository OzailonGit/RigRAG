"""RAG evaluation set.

Each case pairs a query with ground truth that can be checked
deterministically against the seed catalog: whether the expected
product(s) appear in the top-k results. Answer quality is out of scope
since it would need an LLM judge (see run_eval.py).
"""

from dataclasses import dataclass, field


@dataclass
class EvalCase:
    query: str
    category: str | None
    max_price_php: float | None
    expected_models: list[str]  # any one of these appearing in top-k counts as a hit
    k: int = 5
    notes: str = ""


EVAL_CASES: list[EvalCase] = [
    EvalCase(
        query="good 1440p GPU with lots of VRAM",
        category="gpu",
        max_price_php=None,
        expected_models=["RTX 4060 Ti 16GB", "Radeon RX 7800 XT", "RTX 4070 Super", "Radeon RX 9060 XT"],
        notes="High-VRAM mid/high tier GPUs; any of these being surfaced counts as relevant.",
    ),
    EvalCase(
        query="quiet efficient CPU for a small form factor build",
        category="cpu",
        max_price_php=None,
        expected_models=["Ryzen 5 7600", "Core i5-12400", "Ryzen 5 5600"],
        notes="Low-TDP entry/mid CPUs (65W) should rank above the 125W i7-13700K.",
    ),
    EvalCase(
        query="budget graphics card under 20000 pesos",
        category="gpu",
        max_price_php=20000,
        expected_models=["RTX 4060", "Radeon RX 7600"],
    ),
    EvalCase(
        query="motherboard with WiFi for AM5 processors",
        category="motherboard",
        max_price_php=None,
        expected_models=["MAG B650 Tomahawk WiFi"],
        notes="Only AM5 board in the seed catalog with wifi=true.",
    ),
    EvalCase(
        query="fast NVMe SSD for gaming",
        category="storage",
        max_price_php=None,
        expected_models=["Black SN770 1TB", "NV2 500GB"],
    ),
    EvalCase(
        query="power supply with enough headroom for a high-end GPU",
        category="psu",
        max_price_php=None,
        expected_models=["Focus GX-750", "MWE 650 Gold V2"],
    ),
]
