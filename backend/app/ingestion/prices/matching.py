"""Decides whether a retailer listing is the catalog product, and turns
accepted listings into one price.

Marketplace titles are noisy ("ASUS Dual GeForce RTX 4060 OC 8GB GDDR6 Graphics
Card GPU ✅ Free Shipping"), so matching works on tokens rather than exact
strings. A listing is accepted when one of these holds:
- it contains the manufacturer part number, or
- it names the brand, every significant model number (3+ digits, like
  "4060", "b650", "750"), and most of the model's words.

On top of that, listings for used, refurbished, or partial items are
rejected, GPU variants must agree ("4060" never matches "4060 Ti"), and
capacities must agree for storage and RAM.
"""

import re
import statistics
from decimal import Decimal

from app.ingestion.prices.base import Listing

MIN_WORD_FRACTION = 0.6

REJECT_PATTERNS = [
    r"\bused\b", r"\b2nd\s*hand\b", r"\bsecond\s*hand\b", r"\bpre[\s-]?owned\b", r"\brefurbished\b",
    r"\bdefective\b", r"\bfor parts\b", r"\bbox only\b", r"\bempty box\b", r"\briser\b",
    r"\bsticker\b", r"\bbundle\b", r"\bpc build\b", r"\bsystem unit\b", r"\blaptop\b", r"\brepair\b",
]
# Words like "cable", "bracket", and "backplate" are deliberately absent: real
# PSU and GPU titles use them ("modular cables", "metal backplate"). Cheap
# accessory listings are caught by the model-number checks and drop_outliers.
_REJECT = re.compile("|".join(REJECT_PATTERNS))

GPU_VARIANT_WORDS = {"ti", "super", "xt", "xtx", "gre"}
_STOPWORDS = {"the", "and", "with", "for", "of", "edition", "series", "black", "white", "kit"}

# Drop prices this far from the median before aggregating (needs 3+ listings).
OUTLIER_LOW, OUTLIER_HIGH = 0.6, 1.8


def tokens(text: str) -> list[str]:
    """Lowercase letter runs and digit runs: "RTX4060-O8G" -> ["rtx", "4060", "o", "8", "g"]."""
    return re.findall(r"[a-z]+|\d+", text.lower())


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _gpu_variant_ok(chip: str, title_tokens: list[str]) -> bool:
    chip_tokens = tokens(chip)
    numbers = [t for t in chip_tokens if t.isdigit() and len(t) >= 3]
    if not numbers:
        return True
    number = numbers[-1]
    expected = {t for t in chip_tokens if t in GPU_VARIANT_WORDS}
    for i, token in enumerate(title_tokens):
        if token != number:
            continue
        following = []
        for next_token in title_tokens[i + 1:i + 3]:
            if next_token in GPU_VARIANT_WORDS:
                following.append(next_token)
            else:
                break
        if set(following) == expected:
            return True
    return False


def _capacity_label(capacity_gb: int) -> set[str]:
    labels = {f"{capacity_gb}gb"}
    if capacity_gb >= 1000 and capacity_gb % 1000 == 0:
        labels.add(f"{capacity_gb // 1000}tb")
    if capacity_gb >= 1024 and capacity_gb % 1024 == 0:
        labels.add(f"{capacity_gb // 1024}tb")
    return labels


def match_score(product: dict, title: str) -> float:
    """Returns 0.0 for a rejected listing, 1.0 for a part-number match, or
    the fraction of model words present for a token match."""
    lowered = title.lower()
    if _REJECT.search(lowered):
        return 0.0

    title_tokens = tokens(title)
    title_token_set = set(title_tokens)
    title_compact = compact(title)
    category = product.get("category")

    if category == "gpu" and product.get("chip") and not _gpu_variant_ok(product["chip"], title_tokens):
        return 0.0
    if category in ("storage", "ram") and product.get("capacity_gb"):
        if not _capacity_label(int(product["capacity_gb"])) & {compact(m) for m in re.findall(r"\d+\s*[gt]b", lowered)}:
            return 0.0

    mpn = compact(product.get("mpn") or "")
    if len(mpn) >= 6 and mpn in title_compact:
        return 1.0

    brand = compact(product.get("manufacturer") or "")
    if brand and brand not in title_compact:
        return 0.0

    model_tokens = tokens(f"{product.get('model', '')} {product.get('chip') or ''}")
    numbers = {t for t in model_tokens if t.isdigit() and len(t) >= 3}
    if not numbers <= title_token_set:
        return 0.0

    words = {t for t in model_tokens if t.isalpha() and len(t) >= 2 and t not in _STOPWORDS and t != brand}
    if not words:
        return 1.0 if numbers else 0.0
    fraction = len(words & title_token_set) / len(words)
    return fraction if fraction >= MIN_WORD_FRACTION else 0.0


def drop_outliers(listings: list[Listing]) -> list[Listing]:
    if len(listings) < 3:
        return listings
    median = statistics.median(float(l.price_php) for l in listings)
    return [l for l in listings if OUTLIER_LOW * median <= float(l.price_php) <= OUTLIER_HIGH * median]


def aggregate_price(listings: list[Listing]) -> Decimal | None:
    """Median price, using only official-store listings when there are any,
    since they are the most likely to be genuine, new, and in stock."""
    if not listings:
        return None
    official = [l for l in listings if l.is_official_store]
    pool = official or listings
    return Decimal(str(statistics.median(float(l.price_php) for l in pool))).quantize(Decimal("0.01"))


def representative_listing(listings: list[Listing], price: Decimal) -> Listing | None:
    """The listing to link a product to: from the same pool aggregate_price
    used, the one priced closest to the median, so the linked page shows
    roughly the price the catalog quotes."""
    with_url = [l for l in listings if l.url]
    official = [l for l in with_url if l.is_official_store]
    pool = official or with_url
    if not pool:
        return None
    return min(pool, key=lambda l: abs(float(l.price_php) - float(price)))
