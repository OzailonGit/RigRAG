"""Merges records from several datasets into one row per part.

Records are grouped by a per-category match key. Within a group, each field
takes the value most independent sources agree on, with source priority
(sources.SOURCES) breaking ties. The two PCPartPicker dumps count as one
source, since they share an upstream. Majority beats priority because even
official exports have errors: AMD's lists the A10-6800B at 45 W while three
other sources agree on 100 W. Disagreements between sources are kept in a `conflicts`
column instead of being silently dropped, and values that were inferred
rather than stated (e.g. a chipset read from a board's name) are listed in
`derived_fields`.

Match keys are deliberately strict: two rows merge only when their names and
variant-defining specs agree, so a near-miss produces a duplicate row rather
than a wrong merge.
"""

import re
from collections import defaultdict

from app.ingestion.datasets.sources import SOURCE_RANK
from app.ingestion.normalization.gpu_chips import chip_key

SCHEMAS: dict[str, list[str]] = {
    "cpu": ["name", "manufacturer", "socket", "cores", "threads", "base_clock_ghz", "boost_clock_ghz", "tdp_w",
            "integrated_graphics", "memory_types", "microarchitecture", "release", "msrp_usd", "price_usd", "price_eur"],
    "gpu_chip": ["name", "vendor", "architecture", "vram_gb", "memory_type", "bus_interface", "tdp_w", "suggested_psu_w",
                 "power_connectors", "release", "msrp_usd"],
    "gpu": ["full_name", "name", "manufacturer", "chip", "chip_vendor", "vram_gb", "length_mm", "core_clock_mhz",
            "boost_clock_mhz", "tdp_w", "color", "price_usd", "price_eur"],
    "motherboard": ["name", "manufacturer", "socket", "chipset", "form_factor", "ram_type", "max_ram_gb", "ram_slots",
                    "m2_slots", "color", "price_usd", "price_eur"],
    "ram": ["name", "manufacturer", "ddr_gen", "speed_mts", "modules", "module_gb", "capacity_gb", "cas_latency",
            "first_word_latency_ns", "color", "price_usd", "price_eur"],
    "storage": ["name", "manufacturer", "type", "capacity_gb", "interface", "form_factor", "cache_mb", "read_mb_s",
                "write_mb_s", "price_usd", "price_eur"],
    "psu": ["name", "manufacturer", "wattage", "efficiency", "modular", "form_factor", "color", "price_usd", "price_eur"],
    "cooler": ["name", "manufacturer", "type", "radiator_mm", "fan_rpm", "noise_db", "color", "price_usd", "price_eur"],
    "case": ["name", "manufacturer", "type", "supported_form_factors", "max_gpu_length_mm", "max_cooler_height_mm",
             "side_panel", "volume_l", "internal_35_bays", "psu_included", "color", "price_usd", "price_eur"],
}
EXTRA_COLUMNS = ["sources", "conflicts", "derived_fields", "pcpartpicker_url"]

# Prices legitimately differ between sources (currency, date), and iGPU names
# are spelled a dozen ways, so these never count as conflicts.
_NO_CONFLICT = {"name", "full_name", "manufacturer", "price_usd", "price_eur", "msrp_usd", "release", "color", "integrated_graphics"}
_NUMERIC_TOLERANCE = 0.05

# Sources that share an upstream vote once.
_VOTE_GROUP = {"pcpartpicker_hf": "pcpartpicker", "pcpartpicker_docyx": "pcpartpicker"}

# Canonical chip names ("GeForce RTX 4060") come from the GPU databases rather
# than editorial shorthand ("NVIDIA RTX 4060").
_NAME_PREFERENCE = {"gpu_chip": ["techpowerup_gpus", "wikipedia_gpus"]}

# Chip-name suffixes that mark a minor revision with the same specs class:
# LHR (hash-rate limited), G5/G6 memory variants, "GHz Edition", and "- 512"-style shader counts.
_CHIP_REVISION = re.compile(r"(?i)\s+(lhr|g5x?|g6x?|ghz edition)\b|\s+-\s+\d+\b")


def compact(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def cpu_key(name: str) -> str:
    """ "Intel® Core™ i5-13600K Processor", "Intel Core i5 13600K 3.5 GHz 14-Core LGA1700"
    and "Core i5-13600K" all produce "corei513600k"."""
    s = re.sub(r"[™®©]", "", name.lower())
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\bwith\b.*$", " ", s)
    s = re.sub(r"\b\d+(\.\d+)?\s*ghz\b|\b\d+-core\b|\boem/tray\b|\b(oem|tray|boxed|box|processor|apu|desktop)\b", " ", s)
    s = re.sub(r"\b(fc)?lga\s?\d+(-\d)?\b|\b(am[1-5]|fm[12])\+?\b|\bstrx?\d\b|\btr4\b", " ", s)
    s = re.sub(r"\b(amd|intel)\b", " ", s)
    return compact(s)


def gpu_chip_key(name: str) -> str:
    name = _CHIP_REVISION.sub("", name)
    return chip_key(re.sub(r"(?i)\s*\d+\s*gb\b", "", name))


def _vram(value) -> str:
    return str(int(value)) if value else ""


def match_key(category: str, r: dict) -> str:
    if category == "cpu":
        return cpu_key(r["name"])
    if category == "gpu_chip":
        return f"{gpu_chip_key(r['name'])}|{_vram(r.get('vram_gb'))}"
    if category == "gpu":
        return f"{compact(r['name'])}|{gpu_chip_key(r.get('chip') or '')}|{_vram(r.get('vram_gb'))}|{compact(r.get('color'))}"
    if category == "motherboard":
        return compact(r["name"])
    if category == "ram":
        return "|".join(compact(r.get(k)) for k in ("name", "ddr_gen", "speed_mts", "modules", "module_gb", "cas_latency", "color"))
    if category == "storage":
        return "|".join(compact(r.get(k)) for k in ("name", "capacity_gb", "interface", "form_factor", "cache_mb"))
    if category == "psu":
        return "|".join(compact(r.get(k)) for k in ("name", "wattage", "efficiency", "modular", "color"))
    if category == "cooler":
        return "|".join(compact(r.get(k)) for k in ("name", "radiator_mm", "color"))
    if category == "case":
        # Same-named cases are sold with and without a bundled PSU, and in several tower sizes.
        return "|".join(compact(r.get(k)) for k in ("name", "color", "side_panel", "type", "psu_included"))
    raise ValueError(category)


def _tokens(value) -> set[str]:
    return set(re.findall(r"[a-z]+|\d+(?:\.\d+)?", str(value).lower()))


def _differs(a, b) -> bool:
    """Numbers differ beyond a 5% tolerance. Strings differ unless one is a
    less specific form of the other ("PCIe x16" vs "PCIe 2.0 x16", "AGP" vs "AGP 4x")."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        biggest = max(abs(a), abs(b))
        return biggest > 0 and abs(a - b) / biggest > _NUMERIC_TOLERANCE
    if compact(a) == compact(b):
        return False
    ta, tb = _tokens(a), _tokens(b)
    return not (ta and tb and (ta <= tb or tb <= ta))


def _choose(candidates: list[tuple[str, object, dict]]) -> tuple[str, object, dict]:
    """Picks the value backed by the most vote groups; earlier (higher-priority)
    candidates win ties. Each vote group counts once, using its best record."""
    seen_groups = set()
    voters = []
    for candidate in candidates:
        group = _VOTE_GROUP.get(candidate[0], candidate[0])
        if group not in seen_groups:
            seen_groups.add(group)
            voters.append(candidate)

    best, best_votes = candidates[0], 0
    for candidate in voters:
        votes = sum(1 for other in voters if not _differs(candidate[1], other[1]))
        if votes > best_votes:
            best, best_votes = candidate, votes
    return best


def merge_group(category: str, records: list[tuple[str, dict]]) -> dict:
    records = sorted(records, key=lambda sr: SOURCE_RANK[sr[0]])
    row: dict = {}
    conflicts, derived = [], []
    for field in SCHEMAS[category]:
        candidates = [(src, rec[field], rec) for src, rec in records if rec.get(field) not in (None, "")]
        if not candidates:
            row[field] = None
            continue
        if field == "name" and category in _NAME_PREFERENCE:
            preferred = _NAME_PREFERENCE[category]
            candidates.sort(key=lambda c: preferred.index(c[0]) if c[0] in preferred else len(preferred))
            chosen_src, chosen, chosen_rec = candidates[0]
        else:
            chosen_src, chosen, chosen_rec = _choose(candidates)
        if field == "socket" and chosen == "LGA1151" and any(v == "LGA1151-2" for _, v, _ in candidates):
            # 8th/9th-gen Intel needs the v2 revision of LGA1151; the specific label wins.
            chosen = "LGA1151-2"
        row[field] = chosen
        if field in chosen_rec.get("_derived", []):
            derived.append(field)
        if field not in _NO_CONFLICT:
            others = [(src, v) for src, v, _ in candidates if _differs(chosen, v)]
            if others:
                shown = [f"{chosen}@{chosen_src}"] + [f"{v}@{src}" for src, v in others[:3]]
                conflicts.append(f"{field}: {' vs '.join(shown)}")

    row["sources"] = ", ".join(dict.fromkeys(src for src, _ in records))
    row["conflicts"] = "; ".join(conflicts) or None
    row["derived_fields"] = ", ".join(derived) or None
    row["pcpartpicker_url"] = next((rec["pcpartpicker_url"] for _, rec in records if rec.get("pcpartpicker_url")), None)
    return row


def merge_category(category: str, records: list[tuple[str, dict]]) -> list[dict]:
    groups: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for source, record in records:
        groups[match_key(category, record)].append((source, record))

    if category == "gpu_chip":
        # A source that omits VRAM joins the single sized variant of the same chip, if unambiguous.
        sized: dict[str, list[str]] = defaultdict(list)
        for key in groups:
            base, vram = key.split("|")
            if vram:
                sized[base].append(key)
        for key in [k for k in groups if k.endswith("|")]:
            targets = sized.get(key[:-1], [])
            if len(targets) == 1:
                groups[targets[0]].extend(groups.pop(key))

    return [merge_group(category, group) for group in groups.values()]


def attach_chip_specs(cards: list[dict], chips: list[dict]) -> None:
    """Fills each card's TDP (and vendor, if missing) from its GPU chip."""
    by_key = {}
    by_base: dict[str, list[dict]] = defaultdict(list)
    for chip in chips:
        base = gpu_chip_key(chip["name"])
        by_key[f"{base}|{_vram(chip.get('vram_gb'))}"] = chip
        by_base[base].append(chip)

    for card in cards:
        if not card.get("chip"):
            continue
        base = gpu_chip_key(card["chip"])
        chip = by_key.get(f"{base}|{_vram(card.get('vram_gb'))}")
        if chip is None:
            # No exact memory-size match: use the chip if every sized variant agrees on TDP.
            variants = by_base.get(base, [])
            tdps = {v["tdp_w"] for v in variants if v.get("tdp_w") is not None}
            if len(variants) == 1 or len(tdps) == 1:
                chip = next((v for v in variants if v.get("tdp_w") is not None), variants[0] if variants else None)
        if chip is None:
            continue
        if card.get("tdp_w") is None and chip.get("tdp_w") is not None:
            card["tdp_w"] = chip["tdp_w"]
            card["derived_fields"] = ", ".join(filter(None, [card.get("derived_fields"), "tdp_w"]))
        card["chip_vendor"] = card.get("chip_vendor") or chip.get("vendor")


def add_full_names(cards: list[dict]) -> None:
    """PCPartPicker card names often omit the chip ("Sapphire PULSE"), so
    full_name appends it when missing."""
    for card in cards:
        name, chip = card["name"], card.get("chip") or ""
        missing = chip and gpu_chip_key(chip) not in compact(name)
        card["full_name"] = f"{name} {chip}" if missing else name
