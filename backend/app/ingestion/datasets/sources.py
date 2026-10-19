"""Registry of public PC-part datasets combined by app.ingestion.datasets.build.

SOURCES is in field-priority order: when two sources describe the same part,
the earlier source's value wins (official manufacturer data first, scraped
retail listings last). Each source keeps its own license; the combined output
is for local use and is not committed (data/combined/ is gitignored).
"""

from dataclasses import dataclass, field
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[4]
RAW_DIR = REPO_ROOT / "data" / "raw" / "datasets"

_FELIX = "https://raw.githubusercontent.com/felixsteinke/cpu-spec-dataset/HEAD/dataset"
_DOCYX = "https://raw.githubusercontent.com/docyx/pc-part-dataset/main/data/json"
DOCYX_CATEGORIES = ["cpu", "video-card", "motherboard", "memory", "internal-hard-drive", "power-supply", "cpu-cooler", "case"]


@dataclass(frozen=True)
class Source:
    name: str
    title: str
    homepage: str
    license: str
    origin: str
    as_of: str
    files: dict[str, str] = field(default_factory=dict)


SOURCES = [
    Source(
        name="amd_official",
        title="AMD processor specifications (export via felixsteinke/cpu-spec-dataset)",
        homepage="https://github.com/felixsteinke/cpu-spec-dataset",
        license="Repository AGPL-3.0; data from amd.com",
        origin="AMD product specification pages",
        as_of="2023-07",
        files={"amd-cpus.csv": f"{_FELIX}/amd-cpus.csv"},
    ),
    Source(
        name="intel_ark",
        title="Intel ARK specifications (via felixsteinke/cpu-spec-dataset)",
        homepage="https://github.com/felixsteinke/cpu-spec-dataset",
        license="Repository AGPL-3.0; data from ark.intel.com",
        origin="Intel ARK",
        as_of="2023-07",
        files={"intel-cpus.csv": f"{_FELIX}/intel-cpus.csv"},
    ),
    Source(
        name="iblessi",
        title="iBlessi/pc-builder-parts (TechFuelHQ PC Builder dataset)",
        homepage="https://github.com/iBlessi/pc-builder-parts",
        license="CC BY 4.0",
        origin="Hand-verified against manufacturer pages",
        as_of="2026-07",
        files={"pc-builder-parts.json": "https://raw.githubusercontent.com/iBlessi/pc-builder-parts/main/pc-builder-parts.json"},
    ),
    Source(
        name="bottleneckpc",
        title="BottleneckPC hardware dataset",
        homepage="https://bottleneckpc.com/data",
        license="CC BY 4.0",
        origin="BottleneckPC editorial data",
        as_of="2026-07",
        files={"data.json": "https://bottleneckpc.com/data.json"},
    ),
    Source(
        name="wikipedia_gpus",
        title="voidful/gpu-info-api (Wikipedia GPU tables)",
        homepage="https://github.com/voidful/gpu-info-api",
        license="CC BY-SA (Wikipedia); repository unlicensed",
        origin="Wikipedia lists of NVIDIA, AMD, and Intel GPUs",
        as_of="2026-10",
        files={"gpu.json": "https://raw.githubusercontent.com/voidful/gpu-info-api/main/gpu.json"},
    ),
    Source(
        name="techpowerup_gpus",
        title="Jr23xd23/gpu-database (TechPowerUp via dbgpu)",
        homepage="https://huggingface.co/datasets/Jr23xd23/gpu-database",
        license="Apache-2.0 label; data from TechPowerUp GPU Database",
        origin="TechPowerUp GPU Database",
        as_of="2026-01",
        files={"all-gpus.json": "https://huggingface.co/datasets/Jr23xd23/gpu-database/resolve/main/data/all-gpus.json"},
    ),
    Source(
        name="pcpartpicker_hf",
        title="Doshiba/pcpartpicker-parts-dataset (identical copy: ThigasFS)",
        homepage="https://huggingface.co/datasets/Doshiba/pcpartpicker-parts-dataset",
        license="MIT label; data scraped from PCPartPicker",
        origin="PCPartPicker (fr), prices in EUR",
        as_of="2026-05",
        files={"pcpartpicker_parts.jsonl": "https://huggingface.co/datasets/Doshiba/pcpartpicker-parts-dataset/resolve/main/pcpartpicker_parts.jsonl"},
    ),
    Source(
        name="pcpartpicker_docyx",
        title="docyx/pc-part-dataset",
        homepage="https://github.com/docyx/pc-part-dataset",
        license="MIT label; data scraped from PCPartPicker",
        origin="PCPartPicker (us), prices in USD",
        as_of="2025-07",
        files={f"{c}.json": f"{_DOCYX}/{c}.json" for c in DOCYX_CATEGORIES},
    ),
    Source(
        name="mehax",
        title="mehax/pc_build CPU dataset",
        homepage="https://huggingface.co/datasets/mehax/pc_build",
        license="ODC-By",
        origin="Unstated",
        as_of="2025-05",
        files={"CPU.json": "https://huggingface.co/datasets/mehax/pc_build/resolve/main/CPU.json"},
    ),
    Source(
        name="passmark",
        title="PassMark CPU list (via felixsteinke/cpu-spec-dataset)",
        homepage="https://github.com/felixsteinke/cpu-spec-dataset",
        license="Repository AGPL-3.0; data from cpubenchmark.net",
        origin="PassMark cpubenchmark.net",
        as_of="2023-07",
        files={"benchmark-cpus.csv": f"{_FELIX}/benchmark-cpus.csv"},
    ),
]

SOURCE_RANK = {s.name: i for i, s in enumerate(SOURCES)}

# Found but not combined, with the reason (reported in the manifest).
EXCLUDED = {
    "OpenML Newegg (43836)": "category labels are wrong (e.g. RAM filed under cpu) and descriptions are truncated",
    "felixsteinke cpuworld-cpus.csv": "243 rows, almost all server Xeons, in a pivoted layout",
    "ThigasFS/pcpartpicker-parts-dataset": "byte-for-byte copy of Doshiba/pcpartpicker-parts-dataset",
    "dbgpu (PyPI)": "same data as Jr23xd23/gpu-database, which is included",
    "BaraaZ95/Techpowerup-scraper": "scraper code only, no data files",
    "Kaggle datasets": "download requires a Kaggle account",
    "pchardware.org": "terms prohibit automated extraction",
}


def fetch(source: Source, raw_dir: Path = RAW_DIR, force: bool = False, http: httpx.Client | None = None) -> Path:
    """Downloads a source's files (cached) and returns its directory."""
    target = raw_dir / source.name
    target.mkdir(parents=True, exist_ok=True)
    client = http or httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "RigRAG dataset builder"})
    for filename, url in source.files.items():
        path = target / filename
        if path.exists() and not force:
            continue
        response = client.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)
    return target
