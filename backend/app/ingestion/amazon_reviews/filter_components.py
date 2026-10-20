"""Cuts the Amazon Reviews 2023 Electronics files down to desktop PC parts.

    python -m app.ingestion.amazon_reviews.filter_components \
        --meta ~/Downloads/meta_Electronics.jsonl --reviews ~/Downloads/Electronics.jsonl

Writes data/raw/amazon/ (gitignored):
  products.jsonl   one row per product in a catalog category, with the
                   fields useful for matching it to catalog parts
  reviews.jsonl    every review of those products, cleaned
  summary.json     counts per category, and what was dropped and why

The dataset (McAuley Lab, UCSD; Hou et al. 2024, "Bridging Language and
Items for Retrieval and Recommendation", arXiv:2403.03952) states no
license; it was released for research, and reviewers keep the copyright in
their text. So the raw files and these extracts stay out of the repo, and
review text is never shown verbatim in replies. Reviews end in September
2023, so parts released later have none.

Products are kept by their Amazon category path, mapped onto the catalog's
categories in CATEGORY_PATHS. Laptop parts, case fans, optical drives, and
external and USB storage are left out, as are products filed under no
narrower path than "Internal Components". Amazon's categories are loose
(power supply cables, laptop fans, and server memory sit beside the parts
themselves), so NOT_A_PART then drops titles that name an accessory, a
laptop or server part, or a part made for one brand's systems, and
refurbished listings, whose reviews are about the refurbisher.
"""

import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.ingestion.datasets.sources import REPO_ROOT

OUTPUT_DIR = REPO_ROOT / "data" / "raw" / "amazon"

# Catalog category -> Amazon category paths (a prefix of the product's path).
CATEGORY_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "cpu": (("Computer Components", "Internal Components", "CPU Processors"),),
    "gpu": (("Computer Components", "Internal Components", "Graphics Cards"),),
    "motherboard": (("Computer Components", "Internal Components", "Motherboards"),),
    "ram": (("Computer Components", "Internal Components", "Memory"),),
    "psu": (("Computer Components", "Internal Components", "Internal Power Supplies"),),
    "cooler": (
        ("Computer Components", "Internal Components", "Fans & Cooling", "CPU Cooling Fans"),
        ("Computer Components", "Internal Components", "Fans & Cooling", "Water Cooling Systems"),
        ("Computer Components", "Internal Components", "Fans & Cooling", "Heatsinks"),
    ),
    "case": (("Computer Components", "External Components", "Computer Cases"),),
    "storage": (
        ("Data Storage", "Internal Solid State Drives"),
        ("Data Storage", "Internal Hard Drives"),
    ),
}

_REFURBISHED = r"\b(?:renewed|refurbished)\b"
# Laptop and server parts, and parts made for one brand's systems. Not used
# for storage: most desktop SSD titles say "for PC or laptop".
_OTHER_SYSTEMS = (r"\b(?:laptop|notebook|macbook|imac|chromebook|thinkpad|poweredge|proliant|"
                  r"for (?:dell|hp|hewlett|lenovo|toshiba|acer|apple|compaq|sony|gateway|ibm))\b")
_NOT_DESKTOP = _REFURBISHED + "|" + _OTHER_SYSTEMS + r"|\breplacement\b"
# Not used for power supplies (which include cables and testers) or cases
# (whose titles praise their cable management).
_ACCESSORY = r"\b(?:cables?|adapters?|extensions?|splitters?|brackets?|screws?|standoffs?|sleeved)\b"
# Category -> title pattern of things in that Amazon category that aren't a
# desktop part of that type. Case-insensitive. Where a part's title reliably
# carries a marker (a CPU names its maker, a power supply its wattage), its
# absence is the rule, which catches accessories no word list would.
NOT_A_PART: dict[str, str] = {
    "cpu": _NOT_DESKTOP + r"|^(?!.*\b(?:intel|amd|core i\d|pentium|celeron|xeon|ryzen|athlon|phenom|opteron|"
                          r"threadripper|fx-\d))",
    "gpu": _NOT_DESKTOP + r"|^(?!.*\b(?:geforce|radeon|rtx|gtx|gt \d+|quadro|arc|firepro|titan)\b)"
                          r"|\b(?:riser|holder|sag|brace|cooling fan|fan for)\b",
    "motherboard": _NOT_DESKTOP + "|" + _ACCESSORY,
    "ram": _NOT_DESKTOP + r"|\b(?:so-?dimm|server|registered|rdimm|lrdimm|fb-?dimm)\b|(?<!non-)\becc\b",
    # A power supply states its wattage, or at least its 80 Plus rating or
    # form factor; without a wattage, an accessory word marks the rest.
    "psu": _NOT_DESKTOP + r"|^(?!.*\b\d{3,4} ?(?:w|watts?)\b)(?:(?!.*(?:\b80 ?(?:plus|\+)|\b(?:atx|sfx) power supply))"
                          r"|(?=.*\b(?:raspberry|tester|cables?|switch|adapter)\b))",
    "cooler": _NOT_DESKTOP + "|" + _ACCESSORY + r"|\b(?:hub|controller|fittings?|tubing|tubes?|reservoir|"
                                                r"thermal paste|grease|thermal pads?|water block|cooling block|"
                                                r"gpu|wipes)\b",
    "case": _NOT_DESKTOP + r"|\b(?:rails?|rack ?mount|\d+u|raspberry|arduino|pcduino|enclosure for)\b",
    "storage": _REFURBISHED + r"|\b(?:enclosure|caddy|dock)\b",
}
_NOT_A_PART = {category: re.compile(pattern, re.I) for category, pattern in NOT_A_PART.items()}

# Product fields kept; images, videos, and bought_together aren't needed to
# match a product or read its reviews.
_PRODUCT_FIELDS = ("parent_asin", "title", "store", "average_rating", "rating_number", "price", "features",
                   "description", "details")
_DROPPED_DETAILS = ("Best Sellers Rank",)  # a snapshot ranking, not a property of the product

# Matching on the raw line skips json.loads for the ~99% of reviews that
# belong to other products.
_PARENT_ASIN = re.compile(r'"parent_asin":\s*"([^"]+)"')
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


def catalog_category(categories: list[str]) -> str | None:
    """The catalog category for an Amazon category path, or None. Every
    kept path starts with "Electronics > Computers & Accessories"."""
    if categories[:2] != ["Electronics", "Computers & Accessories"]:
        return None
    path = tuple(categories[2:])
    for category, prefixes in CATEGORY_PATHS.items():
        if any(path[:len(prefix)] == prefix for prefix in prefixes):
            return category
    return None


def clean_text(text: str | None) -> str:
    """Review and product text with HTML line breaks and entities undone
    and whitespace collapsed."""
    if not text:
        return ""
    text = _TAGS.sub(" ", text.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n"))
    lines = (_SPACES.sub(" ", line).strip() for line in html.unescape(text).split("\n"))
    return "\n".join(line for line in lines if line)


def clean_product(record: dict, category: str) -> dict:
    product = {name: record.get(name) for name in _PRODUCT_FIELDS}
    product["category"] = category
    product["amazon_category"] = " > ".join(record.get("categories") or [])
    product["title"] = clean_text(product["title"])
    product["features"] = [clean_text(f) for f in product["features"] or [] if clean_text(f)]
    product["description"] = " ".join(clean_text(d) for d in product["description"] or []).strip()
    details = dict(product["details"] or {})
    for name in _DROPPED_DETAILS:
        details.pop(name, None)
    product["details"] = details
    return product


def clean_review(record: dict) -> dict | None:
    """A review without the reviewer's ID or images, or None when it has
    no text."""
    text = clean_text(record.get("text"))
    if not text:
        return None
    return {
        "parent_asin": record["parent_asin"],
        "asin": record.get("asin"),  # the variant reviewed (an 8GB vs. 16GB card share a parent)
        "rating": record.get("rating"),
        "title": clean_text(record.get("title")),
        "text": text,
        "date": datetime.fromtimestamp(record["timestamp"] / 1000, tz=timezone.utc).date().isoformat(),
        "verified_purchase": bool(record.get("verified_purchase")),
        "helpful_votes": record.get("helpful_vote") or 0,
    }


def filter_products(meta_path: Path, out_path: Path) -> tuple[dict[str, str], Counter]:
    """Writes the products in catalog categories; returns parent_asin ->
    category and the counts for the summary."""
    kept: dict[str, str] = {}
    counts: Counter = Counter()
    with meta_path.open(encoding="utf-8") as source, out_path.open("w", encoding="utf-8") as out:
        for line in source:
            counts["products read"] += 1
            record = json.loads(line)
            category = catalog_category(record.get("categories") or [])
            if category is None:
                continue
            if not (record.get("title") or "").strip():
                counts["dropped: no title"] += 1
                continue
            if _NOT_A_PART[category].search(clean_text(record["title"])):
                counts[f"dropped: not a part ({category})"] += 1
                continue
            if record["parent_asin"] in kept:
                counts["dropped: duplicate parent_asin"] += 1
                continue
            kept[record["parent_asin"]] = category
            counts[f"products: {category}"] += 1
            out.write(json.dumps(clean_product(record, category), ensure_ascii=False) + "\n")
    return kept, counts


def filter_reviews(reviews_path: Path, products: dict[str, str], out_path: Path) -> Counter:
    counts: Counter = Counter()
    seen: set[tuple] = set()
    with reviews_path.open(encoding="utf-8") as source, out_path.open("w", encoding="utf-8") as out:
        for line in source:
            counts["reviews read"] += 1
            match = _PARENT_ASIN.search(line)
            if match is None or match.group(1) not in products:
                continue
            review = clean_review(json.loads(line))
            if review is None:
                counts["dropped: empty review"] += 1
                continue
            key = (review["parent_asin"], review["date"], review["text"])
            if key in seen:  # the same review listed under two variants
                counts["dropped: duplicate review"] += 1
                continue
            seen.add(key)
            counts[f"reviews: {products[review['parent_asin']]}"] += 1
            out.write(json.dumps(review, ensure_ascii=False) + "\n")
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--meta", type=Path, required=True, help="meta_Electronics.jsonl")
    parser.add_argument("--reviews", type=Path, required=True, help="Electronics.jsonl")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    products, product_counts = filter_products(args.meta.expanduser(), args.out / "products.jsonl")
    print(f"Kept {len(products):,} of {product_counts['products read']:,} products", flush=True)
    review_counts = filter_reviews(args.reviews.expanduser(), products, args.out / "reviews.jsonl")
    kept_reviews = sum(v for k, v in review_counts.items() if k.startswith("reviews: "))
    print(f"Kept {kept_reviews:,} of {review_counts['reviews read']:,} reviews")

    summary = {"source": "Amazon Reviews 2023, Electronics (McAuley Lab, UCSD)", **product_counts, **review_counts}
    (args.out / "summary.json").write_text(json.dumps(dict(sorted(summary.items())), indent=2) + "\n")


if __name__ == "__main__":
    main()
