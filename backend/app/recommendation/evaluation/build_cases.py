"""Build evaluation set: requests with expectations checked by hand.

Every case is checked for compatibility (no blocking issue in any build
shown), budget (the recommended build at or under it; alternatives within
their own limits), and response time. Each case also sets the lowest CPU
and GPU tier an acceptable answer needs, chosen by pricing the cheapest
parts that reach it in the full catalog, so a tier is only expected where
the budget can actually buy it. `checks` add the request's own
constraints (brand, Wi-Fi, memory size, owned parts).

Tiers: 1 entry, 2 mid, 3 high, 4 enthusiast (scoring/tiers.py).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from app.recommendation.engine import BuildResult
from app.schemas import BuildRequest, Resolution, UseCase


@dataclass
class BuildCase:
    name: str
    request: BuildRequest
    min_cpu_tier: int = 1
    min_gpu_tier: int = 1
    expect_build: bool = True
    # A category the user already owns a part in; the eval picks a priced one.
    owns: str | None = None
    note: str = ""  # how the expectation was checked against catalog prices
    # (description, check) pairs on the result
    checks: list[tuple[str, Callable[[BuildResult], bool]]] = field(default_factory=list)


def _primary(result: BuildResult):
    return result.primary.components


def _gaming(budget: float, resolution: Resolution | None = None, **extra) -> BuildRequest:
    return BuildRequest(budget_php=budget, use_case=[UseCase.gaming], resolution=resolution, **extra)


BUILD_CASES: list[BuildCase] = [
    BuildCase("₱25k general use", BuildRequest(budget_php=25000, use_case=[UseCase.general]), min_cpu_tier=2,
              note="A Ryzen 5 5600G (₱9,490, mid tier, integrated graphics) with no separate GPU fits; "
                   "with an Arc A380 added it comes to ₱29,760."),
    BuildCase("₱30k gaming", _gaming(30000), min_cpu_tier=2, min_gpu_tier=2,
              note="A Ryzen 5 5600 (₱5,500) with an Arc A750 comes to ₱29,740. The 5600 is mid tier in practice "
                   "but has no benchmark in the catalog yet, so it is rated entry."),
    BuildCase("₱40k 1440p gaming", _gaming(40000, Resolution.r1440p), min_cpu_tier=2, min_gpu_tier=2,
              note="The cheapest high-tier GPU (RTX 5060 Ti 8GB, ₱20,260) leaves about ₱19,700, just short of the "
                   "rest of a build, so mid tier is the expectation."),
    BuildCase("₱50k 1080p gaming", _gaming(50000, Resolution.r1080p), min_cpu_tier=2, min_gpu_tier=2),
    BuildCase("₱70k 1440p gaming", _gaming(70000, Resolution.r1440p), min_cpu_tier=2, min_gpu_tier=3),
    BuildCase("₱120k 1440p gaming", _gaming(120000, Resolution.r1440p), min_cpu_tier=3, min_gpu_tier=4,
              note="An RX 9070 XT (₱39,130, enthusiast) fits with room for a high-tier CPU."),
    BuildCase("₱150k 4K gaming", _gaming(150000, Resolution.r4k), min_cpu_tier=3, min_gpu_tier=4),
    BuildCase("₱600k gaming", _gaming(600000), min_cpu_tier=4, min_gpu_tier=4, checks=[
        ("spends 85% of the budget or says why not",
         lambda r: r.primary.score.total_price_php >= 510000 or r.unspent_reason is not None),
    ]),
    BuildCase("₱85k video editing", BuildRequest(budget_php=85000, use_case=[UseCase.video_editing]),
              min_cpu_tier=3, min_gpu_tier=2, checks=[
                  ("at least 32GB of memory", lambda r: _primary(r).ram["capacity_gb"] >= 32),
              ]),
    BuildCase("₱100k 3D rendering", BuildRequest(budget_php=100000, use_case=[UseCase.rendering_3d]),
              min_cpu_tier=3, min_gpu_tier=3, checks=[
                  ("at least 32GB of memory", lambda r: _primary(r).ram["capacity_gb"] >= 32),
              ]),
    BuildCase("₱90k AI/ML", BuildRequest(budget_php=90000, use_case=[UseCase.ai_ml]),
              min_cpu_tier=2, min_gpu_tier=3, checks=[
                  ("at least 32GB of memory", lambda r: _primary(r).ram["capacity_gb"] >= 32),
                  ("a GPU with 12GB+ VRAM", lambda r: (_primary(r).gpu or {}).get("vram_gb", 0) >= 12),
              ]),
    BuildCase("₱70k gaming, AMD CPU and NVIDIA GPU",
              _gaming(70000, cpu_brand="AMD", gpu_brand="NVIDIA"), min_cpu_tier=2, min_gpu_tier=3, checks=[
                  ("an AMD CPU", lambda r: _primary(r).cpu["manufacturer"].lower() == "amd"),
                  ("an NVIDIA GPU", lambda r: (_primary(r).gpu or {}).get("chip_vendor", "").upper() == "NVIDIA"),
              ]),
    BuildCase("₱60k gaming with Wi-Fi", _gaming(60000, wifi_required=True), min_cpu_tier=2, min_gpu_tier=2, checks=[
        ("a board with Wi-Fi", lambda r: bool(_primary(r).motherboard.get("wifi"))),
    ]),
    BuildCase("₱80k small form factor gaming", _gaming(80000, small_form_factor=True),
              min_cpu_tier=2, min_gpu_tier=3, checks=[
                  ("a Micro-ATX or Mini-ITX board",
                   lambda r: _primary(r).motherboard["form_factor"] in ("Micro-ATX", "Mini-ITX")),
              ]),
    BuildCase("₱50k gaming, fastest CPU", _gaming(50000, favor="cpu"), min_cpu_tier=4, min_gpu_tier=2,
              note="A Ryzen 7 9800X3D (₱23,960) with an Arc A750 comes to ₱49,690."),
    BuildCase("₱120k gaming, fastest GPU", _gaming(120000, favor="gpu"), min_cpu_tier=3, min_gpu_tier=4),
    BuildCase("₱60k gaming around an owned case", _gaming(60000), min_cpu_tier=2, min_gpu_tier=2, owns="case", checks=[
        ("uses the owned case, at no cost", lambda r: bool(_primary(r).case.get("owned"))),
    ]),
    BuildCase("₱500 is impossible", _gaming(500), expect_build=False),
]
