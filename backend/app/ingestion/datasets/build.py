"""Combines every public PC-part dataset in sources.SOURCES into one file per part type.

    python -m app.ingestion.datasets.build              # download (cached) and combine
    python -m app.ingestion.datasets.build --refresh    # re-download every source

Writes data/combined/<part>.csv and <part>.jsonl for cpu, gpu_chip, gpu,
motherboard, ram, storage, psu, cooler, and case, plus manifest.json with each
source's license, row counts, and merge statistics. data/combined/ is
gitignored because the sources carry different licenses.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.ingestion.datasets.loaders import LOADERS, load_pcpartpicker_docyx, load_pcpartpicker_hf
from app.ingestion.datasets.merge import EXTRA_COLUMNS, SCHEMAS, add_full_names, attach_chip_specs, merge_category
from app.ingestion.datasets.sources import EXCLUDED, RAW_DIR, REPO_ROOT, SOURCES, fetch

OUTPUT_DIR = REPO_ROOT / "data" / "combined"


def _hf_brands(path: Path) -> list[str]:
    brands = {record.get("manufacturer") for _, record in load_pcpartpicker_hf(path)}
    return sorted((b for b in brands if b), key=len, reverse=True)


def _write(category: str, rows: list[dict], out_dir: Path) -> None:
    columns = SCHEMAS[category] + EXTRA_COLUMNS
    rows = sorted(rows, key=lambda r: (str(r.get("manufacturer") or "").lower(), str(r.get("name") or "").lower()))
    with (out_dir / f"{category}.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with (out_dir / f"{category}.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({c: row.get(c) for c in columns}, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-download every source")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)

    paths = {}
    for source in SOURCES:
        print(f"fetching {source.name}")
        paths[source.name] = fetch(source, args.raw_dir, force=args.refresh)

    by_category: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    loaded: dict[str, Counter] = {}
    brands = _hf_brands(paths["pcpartpicker_hf"])
    for source in SOURCES:
        counts = Counter()
        loader = LOADERS[source.name]
        records = load_pcpartpicker_docyx(paths[source.name], brands) if source.name == "pcpartpicker_docyx" else loader(paths[source.name])
        for category, record in records:
            if not record.get("name"):
                continue
            by_category[category].append((source.name, record))
            counts[category] += 1
        loaded[source.name] = counts
        print(f"loaded {source.name}: {dict(counts)}")

    merged = {category: merge_category(category, by_category.get(category, [])) for category in SCHEMAS}
    attach_chip_specs(merged["gpu"], merged["gpu_chip"])
    add_full_names(merged["gpu"])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stats = {}
    for category, rows in merged.items():
        _write(category, rows, args.out_dir)
        stats[category] = {
            "input_records": len(by_category.get(category, [])),
            "output_rows": len(rows),
            "rows_from_multiple_sources": sum(1 for r in rows if "," in (r["sources"] or "")),
            "rows_with_conflicts": sum(1 for r in rows if r.get("conflicts")),
        }

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Field values come from the highest-priority source listing them (order below). Respect each source's license.",
        "sources": [
            {"name": s.name, "title": s.title, "homepage": s.homepage, "license": s.license, "origin": s.origin,
             "as_of": s.as_of, "records_loaded": dict(loaded[s.name])}
            for s in SOURCES
        ],
        "excluded": EXCLUDED,
        "categories": stats,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nWrote {args.out_dir}")
    print(f"{'part':12} {'rows':>7} {'from 2+ sources':>16} {'with conflicts':>15}")
    for category, s in stats.items():
        print(f"{category:12} {s['output_rows']:>7} {s['rows_from_multiple_sources']:>16} {s['rows_with_conflicts']:>15}")


if __name__ == "__main__":
    main()
