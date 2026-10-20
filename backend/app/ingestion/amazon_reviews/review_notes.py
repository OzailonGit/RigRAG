"""Matches the filtered Amazon products (filter_components.py) to catalog
parts and turns each matched part's reviews into an owner-review note.

    python -m app.ingestion.amazon_reviews.review_notes

Reads data/raw/amazon/products.jsonl and reviews.jsonl and writes:
  review_notes.json   one note per catalog part with at least MIN_REVIEWS
                      reviews, in the knowledge-notes format (topic
                      "owner_reviews"), loaded alongside knowledge_notes.json
  matches.jsonl       every accepted product -> part match, for checking by hand
                      and for index_reviews
  match_summary.json  matched, ambiguous, and unmatched counts per category

All of it stays in data/raw/amazon/ (gitignored); see filter_components.py
for why. Run index_notes afterwards to embed the new notes.

Matching: the catalog has no part numbers, so a product matches on its
title and store name, using the price-listing matcher (brand, 3+ digit model
numbers, GPU variant, and capacity checks) plus stricter rules, each from a
mismatch found checking matches by hand:
- at least 80% of the model's words must appear, not 60% (a Skywalker Prism
  kit isn't a Skywalker II);
- every digit run in the catalog model must appear ("SETA A2" never matches
  "SETA D1");
- every code mixing letters and digits must appear whole ("Q570M" never
  matches "X570", "A400i" never matches "A400"), except units like "360mm"
  and RAM kit layouts like "2x8", which titles write many ways;
- a CPU's model code must appear exactly ("2600" never matches "2600X");
- CPU and motherboard combo listings match neither part, and an accessory
  for a part ("heatsink cover for NH-U12A") doesn't match the part;
- a generation the model doesn't name ("Storm Scout II" isn't the Storm
  Scout), and a liquid-cooled card the model doesn't name (an "FTW3 Ultra
  Hybrid" isn't the air-cooled FTW3), don't match.
The most specific matching part wins; a product that matches two different
parts equally well is skipped.

A note states counts, never review text: average rating, review count, the
share of low ratings, and how many reviews mention each known problem for
that kind of part (keyword counts, so "not loud" counts as mentioning
noise, which the note says).
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.database.session import SessionLocal
from app.ingestion.amazon_reviews.filter_components import OUTPUT_DIR
from app.ingestion.prices.matching import compact, match_score, tokens
from app.models.product import Product
from app.services.parts_service import to_full_dict

REVIEW_NOTES = OUTPUT_DIR / "review_notes.json"
MIN_REVIEWS = 20
DATASET_END = "September 2023"
# Themes reported in a note: at least this share of reviews and this many
# reviews (one or two mentions in 40 reviews is noise), at most this many themes.
MIN_THEME_SHARE = 0.01
MIN_THEME_REVIEWS = 3
MAX_THEMES = 4

# Theme -> pattern, per category. Each counts reviews mentioning the theme.
_DOA = r"\b(?:dead on arrival|doa|arrived dead|never worked|didn'?t work out of the box|(?:would ?n'?t|wont|won'?t) (?:power|turn) on)\b"
_FAILED = r"\b(?:stopped working|died after|failed after|quit working|rma'?d|had to rma)\b"
# Complaints and praise are counted apart: most reviews that say "noise"
# say there's none.
_LOUD = (r"\b(?:noisy|(?:too|very|so|really|extremely|quite|pretty|super|annoyingly) loud|louder than|"
         r"loud (?:hum|buzz|whine|fan|pump))\b")
_QUIET = r"\b(?:quiet|silent|whisper)\w*"
_OVERHEAT = r"\b(?:overheat\w*|runs? (?:very |really |too )?hot|thermal throttl\w*)\b"
_CRASH = r"\b(?:crash\w*|blue screens?|bsod|black screens?)\b"
THEMES: dict[str, dict[str, str]] = {
    "gpu": {"say it arrived dead or never worked": _DOA, "say it stopped working later": _FAILED, "mention coil whine": r"\bcoil whine\b",
            "call it loud or noisy": _LOUD, "call it quiet or silent": _QUIET, "mention overheating": _OVERHEAT, "mention crashes or black screens": _CRASH,
            "mention driver problems": r"\bdrivers?\b[^.]{0,40}\b(?:issue|problem|crash|bug)\w*"},
    "cpu": {"say it arrived dead or never worked": _DOA, "say it stopped working later": _FAILED, "mention bent pins": r"\bbent pins?\b",
            "mention overheating": _OVERHEAT, "mention crashes": _CRASH},
    "motherboard": {"say it arrived dead or never worked": _DOA, "say it stopped working later": _FAILED,
                    "mention needing a BIOS update": r"\b(?:bios|uefi)\b[^.]{0,40}\b(?:update|flash)\w*",
                    "mention bent pins": r"\bbent pins?\b", "mention crashes": _CRASH},
    "ram": {"say it arrived dead or never worked": _DOA, "say it stopped working later": _FAILED, "mention crashes": _CRASH,
            "mention XMP or EXPO problems":
                r"\b(?:xmp|expo|docp)\b[^.]{0,40}\b(?:unstable|issue|problem|would ?n'?t|won'?t|crash)\w*"},
    "storage": {"say it arrived dead or never worked": _DOA, "say it failed or stopped working": _FAILED,
                "mention it slowing down": r"\b(?:slow(?:ed|s)? down|slower than)\b", "mention overheating": _OVERHEAT},
    "psu": {"say it arrived dead or never worked": _DOA, "say it stopped working later": _FAILED, "mention coil whine": r"\bcoil whine\b",
            "call it loud or noisy": _LOUD, "call it quiet or silent": _QUIET, "mention random shutdowns": r"\b(?:shuts? (?:off|down)|random(?:ly)? (?:shut|restart|reboot)\w*)"},
    "cooler": {"say it arrived dead or never worked": _DOA, "call it loud or noisy": _LOUD, "call it quiet or silent": _QUIET, "mention rattling or grinding": r"\b(?:rattl\w*|grind\w*)\b",
               "mention leaks or pump failure": r"\b(?:leak\w*|pump (?:noise|failed|died|stopped))\b",  # AIOs only
               "call it hard to mount": r"\b(?:hard|difficult|tricky|a pain) to (?:install|mount)\b"},
    "case": {"say it arrived damaged or with parts missing":
                 r"\b(?:missing (?:screws?|parts?)|arrived (?:damaged|broken|cracked)|shattered|broken glass)\b",
             "call it loud or noisy": _LOUD, "call it quiet or silent": _QUIET, "call it hard to build in": r"\b(?:hard|difficult|tricky|a pain) to (?:install|build|work)\b"},
}
_THEMES = {category: {theme: re.compile(p, re.I) for theme, p in themes.items()} for category, themes in THEMES.items()}


def themes_for(part: dict) -> dict[str, re.Pattern]:
    themes = _THEMES[part["category"]]
    cooler_type = getattr(part.get("type"), "value", part.get("type"))  # an enum from the DB, a string in tests
    if part["category"] == "cooler" and cooler_type != "aio":
        return {name: pattern for name, pattern in themes.items() if name != "mention leaks or pump failure"}
    return themes


def _digit_runs(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def _cpu_code(model: str) -> str | None:
    """"Core i5-14600KF" -> "14600kf", "Ryzen 7 9800X3D" -> "9800x3d"."""
    codes = [t for t in re.split(r"[^a-z0-9]+", model.lower()) if re.search(r"\d{3}", t)]
    return codes[-1] if codes else None


MIN_WORD_SHARE = 0.8
_UNIT = re.compile(r"^\d+(?:mm|w|gb|tb|mhz|mt|cm)$")
_KIT_LAYOUT = re.compile(r"^\d+x\d+$")
_COMBO = {"cpu": re.compile(r"\bmotherboard\b", re.I), "motherboard": re.compile(r"\bprocessor\b|\bcpu\b", re.I)}
_ACCESSORY_FOR = re.compile(r"\b(?:cover|mounting kit|upgrade kit|fan clips?|bracket|adapter|kit) for\b", re.I)
_GENERATIONS = {"ii", "iii", "iv"}
_INTERFACE_GENERATION = re.compile(r"\b(?:sata|ata|usb)\s*(?:ii|iii)\b", re.I)
_LIQUID_GPU = {"hybrid", "hydro", "waterforce", "liquid", "water"}


def _model_codes(model: str) -> set[str]:
    """"B550M-A WIFI II" -> {"b550m"}; "AXP90-X36" -> {"axp90", "x36"}."""
    pieces = (compact(p) for p in re.split(r"[\s\-/()|,]+", model))
    return {p for p in pieces if re.search(r"[a-z]", p) and re.search(r"\d", p)
            and not _UNIT.match(p) and not _KIT_LAYOUT.match(p)}


def match_strength(part: dict, text: str) -> float:
    """0.0 when the listing text isn't this part, else match_score's score."""
    score = match_score(part, text)
    if score < MIN_WORD_SHARE:
        return 0.0
    if not _digit_runs(part["model"]) <= set(tokens(text)):
        return 0.0
    combo = _COMBO.get(part["category"])
    if (combo and combo.search(text)) or _ACCESSORY_FOR.search(text):
        return 0.0
    model_tokens, text_tokens = set(tokens(part["model"])), set(tokens(text))
    # "SATA III" names the interface, not a product generation.
    generation_tokens = set(tokens(_INTERFACE_GENERATION.sub(" ", text)))
    if (generation_tokens & _GENERATIONS) - model_tokens:
        return 0.0
    if part["category"] == "gpu" and (text_tokens & _LIQUID_GPU) - model_tokens:
        return 0.0
    text_compact = compact(text)
    if not all(code in text_compact for code in _model_codes(part["model"])):
        return 0.0
    if part["category"] == "cpu":
        code = _cpu_code(part["model"])
        if code and code not in set(re.split(r"[^a-z0-9]+", text.lower())):
            return 0.0
    return score


@dataclass
class _Catalog:
    by_brand: dict[tuple[str, str], list[dict]] = field(default_factory=lambda: defaultdict(list))

    def add(self, part: dict) -> None:
        self.by_brand[(str(part["category"]), compact(part["manufacturer"]))].append(part)

    def candidates(self, category: str, text: str) -> list[dict]:
        text_compact = compact(text)
        return [part for (cat, brand), parts in self.by_brand.items()
                if cat == category and brand and brand in text_compact for part in parts]


def best_match(catalog: _Catalog, product: dict) -> tuple[list[dict], str]:
    """The catalog parts an Amazon product is, and "matched", "ambiguous",
    or "unmatched". Equal names (catalog duplicates) all match."""
    text = f"{product.get('store') or ''} {product['title']}"
    scored = []
    for part in catalog.candidates(product["category"], text):
        strength = match_strength(part, text)
        if strength:
            scored.append(((strength, len(tokens(part["model"]))), part))
    if not scored:
        return [], "unmatched"
    top = max(key for key, _ in scored)
    winners = [part for key, part in scored if key == top]
    names = {(compact(p["manufacturer"]), compact(p["model"])) for p in winners}
    if len(names) > 1:
        return [], "ambiguous"
    return winners, "matched"


@dataclass
class ReviewStats:
    reviews: int = 0
    rating_total: float = 0.0
    low_ratings: int = 0
    verified: int = 0
    first: str = "9999"
    last: str = "0000"
    themes: Counter = field(default_factory=Counter)

    def add(self, review: dict, themes: dict[str, re.Pattern]) -> None:
        self.reviews += 1
        self.rating_total += review["rating"] or 0
        self.low_ratings += (review["rating"] or 0) <= 2
        self.verified += review["verified_purchase"]
        self.first, self.last = min(self.first, review["date"]), max(self.last, review["date"])
        text = f"{review['title']} {review['text']}"
        for theme, pattern in themes.items():
            if pattern.search(text):
                self.themes[theme] += 1


def _percent(count: int, total: int) -> str:
    share = 100 * count / total
    return f"{share:.0f}%" if share >= 10 else f"{share:.1f}%"


def note_text(name: str, stats: ReviewStats) -> str:
    n = stats.reviews
    text = (f"Amazon US owner reviews of the {name}, from {stats.first[:4]} to {stats.last[:4]}: {n:,} reviews, "
            f"averaging {stats.rating_total / n:.1f} of 5 stars, with {_percent(stats.low_ratings, n)} rating it "
            f"1 or 2 stars and {_percent(stats.verified, n)} from verified purchases.")
    themes = [(theme, count) for theme, count in stats.themes.most_common()
              if count / n >= MIN_THEME_SHARE and count >= MIN_THEME_REVIEWS][:MAX_THEMES]
    if themes:
        listed = "; ".join(f"{_percent(count, n)} ({count:,}) {theme}" for theme, count in themes)
        text += f" Of these reviews, {listed}."
    return text + (" These are keyword counts in owner reviews, not measurements, and a review can mention a "
                   "problem without having it.")


def _note(part: dict, stats: ReviewStats, products: list[dict]) -> dict:
    name = f"{part['manufacturer']} {part['model']}"
    most_reviewed = max(products, key=lambda p: p.get("rating_number") or 0)
    return {
        "id": f"amazon-reviews-{part['id']}",
        "title": f"Amazon owner reviews of the {name}",
        "topic": "owner_reviews",
        "text": note_text(name, stats),
        "applies_to": [{"category": str(part["category"]), "id": str(part["id"])}],
        "sources": [{
            "publisher": "Amazon customer reviews",
            "title": f"{most_reviewed['title'][:120]} (Amazon Reviews 2023, McAuley Lab, UCSD)",
            "url": f"https://www.amazon.com/dp/{most_reviewed['parent_asin']}",
            "kind": "user_reviews",
            "published": f"through {DATASET_END}",
        }],
        "checked": date.today().isoformat(),
    }


def main() -> None:
    db = SessionLocal()
    catalog = _Catalog()
    try:
        for product in db.query(Product).all():
            catalog.add({**to_full_dict(db, product), "category": product.category.value})
    finally:
        db.close()

    counts: Counter = Counter()
    asin_parts: dict[str, list[dict]] = {}
    products_by_asin: dict[str, dict] = {}
    with (OUTPUT_DIR / "products.jsonl").open(encoding="utf-8") as source, \
            (OUTPUT_DIR / "matches.jsonl").open("w", encoding="utf-8") as matches:
        for line in source:
            product = json.loads(line)
            parts, outcome = best_match(catalog, product)
            counts[f"{product['category']}: {outcome}"] += 1
            if parts:
                asin_parts[product["parent_asin"]] = parts
                products_by_asin[product["parent_asin"]] = product
                matches.write(json.dumps({"parent_asin": product["parent_asin"], "amazon_title": product["title"],
                                          "parts": [f"{p['manufacturer']} {p['model']}" for p in parts],
                                          "part_ids": [str(p["id"]) for p in parts]},
                                         ensure_ascii=False) + "\n")

    stats: dict[str, ReviewStats] = defaultdict(ReviewStats)
    part_by_id: dict[str, dict] = {}
    part_products: dict[str, dict[str, dict]] = defaultdict(dict)
    with (OUTPUT_DIR / "reviews.jsonl").open(encoding="utf-8") as source:
        for line in source:
            review = json.loads(line)
            for part in asin_parts.get(review["parent_asin"], []):
                part_id = str(part["id"])
                part_by_id[part_id] = part
                part_products[part_id][review["parent_asin"]] = products_by_asin[review["parent_asin"]]
                stats[part_id].add(review, themes_for(part))

    notes = [_note(part_by_id[pid], s, list(part_products[pid].values()))
             for pid, s in stats.items() if s.reviews >= MIN_REVIEWS]
    for note in notes:
        counts[f"{note['applies_to'][0]['category']}: parts with a note"] += 1
    REVIEW_NOTES.write_text(json.dumps({
        "about": "Owner-review notes generated by app.ingestion.amazon_reviews.review_notes from Amazon Reviews "
                 "2023 (McAuley Lab, UCSD). Counts only; no review text. Not for redistribution.",
        "notes": sorted(notes, key=lambda n: n["id"]),
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "match_summary.json").write_text(json.dumps(dict(sorted(counts.items())), indent=2) + "\n")
    print(json.dumps(dict(sorted(counts.items())), indent=2))
    print(f"Wrote {len(notes):,} owner-review notes to {REVIEW_NOTES}")


if __name__ == "__main__":
    main()
