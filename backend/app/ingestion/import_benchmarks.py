"""Loads relative benchmark scores (data/reference/benchmarks.json) onto
catalog CPUs and GPUs.

    python -m app.ingestion.import_benchmarks

GPUs match on chip name, plus VRAM where the benchmark tests more than one
size ("RTX 5060 Ti 8GB" and "16GB"). CPUs match on model, with Intel F and
KF chips taking the score of the same chip without the suffix. Benchmarked
parts also get their performance tier from their scores (GPUs from 1440p
raster, CPUs from the higher of their gaming and multithreaded tiers), which
replaces the name-based tier the dataset import set. A part with no
benchmark keeps its tier and gets the lowest score of benchmarked parts in
that tier, marked benchmark_estimated, so scoring can treat every part
alike. The lowest, not the median: an untested part could be anywhere in
its tier, and an optimistic guess would let it win on a score it may not have. Re-running replaces every score, so run it after each dataset import.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.cpu import Cpu
from app.models.gpu import Gpu
from app.models.product import PerformanceTier, Product
from app.recommendation.scoring.performance import tier_for_score
from app.recommendation.scoring.tiers import TIER_ORDINAL

REFERENCE = Path(__file__).resolve().parents[3] / "data" / "reference" / "benchmarks.json"
GPU_SCORES = ("raster_1080p", "raster_1440p", "raster_4k")
CPU_SCORES = ("gaming_score", "multithread_score")


def chip_key(chip: str) -> str:
    """"GeForce RTX 4070 SUPER", "RTX 4070 Super", "Intel Arc B580" -> one key."""
    chip = re.sub(r"^(geforce|intel|nvidia|amd)\s+", "", chip.lower().strip())
    return " ".join(re.sub(r"\s*\d+\s*gb$", "", chip).split())


def cpu_key(model: str) -> str:
    """Intel F and KF chips are the non-F chip with graphics disabled."""
    model = " ".join(model.split())
    return re.sub(r"^(Core (?:Ultra \d|i\d)[- ]\d+[A-Z]*?)F$", r"\1", model).lower()


def _tier_floors(rows: list, fields: tuple[str, ...]) -> dict:
    by_tier = defaultdict(lambda: defaultdict(list))
    for spec, tier in rows:
        for name in fields:
            if getattr(spec, name) is not None:
                by_tier[tier][name].append(getattr(spec, name))
    return {tier: {name: min(values) for name, values in scores.items()} for tier, scores in by_tier.items()}


def _fill_from_tier(rows: list, fields: tuple[str, ...]) -> int:
    floors = _tier_floors([(spec, tier) for spec, tier in rows if not spec.benchmark_estimated], fields)
    filled = 0
    for spec, tier in rows:
        for name in fields:
            if getattr(spec, name) is None and name in floors.get(tier, {}):
                setattr(spec, name, floors[tier][name])
                spec.benchmark_estimated = True
        filled += spec.benchmark_estimated
    return filled


def main() -> None:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    gpu_index = defaultdict(dict)
    for entry in reference["gpus"]:
        gpu_index[chip_key(entry["chip"])][entry["vram_gb"]] = entry
    cpu_index = {cpu_key(entry["model"]): entry for entry in reference["cpus"]}

    db = SessionLocal()
    try:
        gpus = db.execute(select(Gpu, Product).join(Product, Product.id == Gpu.product_id)).all()
        matched_gpus = 0
        for gpu, product in gpus:
            sizes = gpu_index.get(chip_key(gpu.chip or ""), {})
            entry = sizes.get(gpu.vram_gb) or sizes.get(None)
            for name in GPU_SCORES:
                setattr(gpu, name, entry[name] if entry else None)
            gpu.benchmark_estimated = False
            if entry:
                tier = PerformanceTier(tier_for_score(entry["raster_1440p"], "raster_1440p"))
                gpu.performance_tier = product.performance_tier = tier
                matched_gpus += 1
        db.flush()
        estimated_gpus = _fill_from_tier([(g, p.performance_tier) for g, p in gpus], GPU_SCORES)

        cpus = db.execute(select(Cpu, Product).join(Product, Product.id == Cpu.product_id)).all()
        matched_cpus = 0
        for cpu, product in cpus:
            entry = cpu_index.get(cpu_key(product.model))
            cpu.gaming_score = entry["gaming_1080p"] if entry else None
            cpu.multithread_score = entry["multithread"] if entry else None
            cpu.benchmark_estimated = False
            if entry:
                tiers = [tier_for_score(score, field) for field, score in
                         (("gaming_score", cpu.gaming_score), ("multithread_score", cpu.multithread_score)) if score is not None]
                product.performance_tier = PerformanceTier(max(tiers, key=TIER_ORDINAL.get))
                matched_cpus += 1
        db.flush()
        estimated_cpus = _fill_from_tier([(c, p.performance_tier) for c, p in cpus], CPU_SCORES)
        db.commit()
    finally:
        db.close()

    print(f"GPUs: {matched_gpus} of {len(gpus)} benchmarked, {estimated_gpus} with a score set to their tier's lowest")
    print(f"CPUs: {matched_cpus} of {len(cpus)} benchmarked, {estimated_cpus} with a score set to their tier's lowest")


if __name__ == "__main__":
    main()
