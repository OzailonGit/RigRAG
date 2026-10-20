"""Sourced hardware notes (data/reference/knowledge_notes.json): trade-offs,
platform quirks, power, noise, and upgrade paths that catalog columns can't
hold, each with the sources it was written from.

A note's applies_to is a list of conditions; a part matches when it fits
every field of any one condition:

    {"category": "gpu", "chip": ["Arc A750"]}           chip names, matched like benchmarks
    {"category": "cpu", "model": ["Ryzen 9 9950X3D"]}   exact models (Intel F/KF fold into the base chip)
    {"category": "cpu", "model_pattern": "^Core i9-14"}  regex on the model
    {"category": "motherboard", "chipset": ["A620"]}    any other part field: one value or a list

    {"category": "gpu", "id": "<uuid>"}                one catalog part

Matching is exact, so a note never reaches a part it wasn't written for.

Owner-review notes (topic "owner_reviews"), generated from Amazon reviews
by app.ingestion.amazon_reviews.review_notes into data/raw/amazon/, load
alongside the sourced notes when that file exists. They state counts from
owner reviews, not facts from a maker or reviewer, and the prompts say so.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.ingestion.import_benchmarks import chip_key, cpu_key

_REPO = Path(__file__).resolve().parents[4]
REFERENCE = _REPO / "data" / "reference" / "knowledge_notes.json"
OWNER_REVIEW_NOTES = _REPO / "data" / "raw" / "amazon" / "review_notes.json"

TOPICS = ("trade_off", "quirk", "power", "noise", "upgrade", "owner_reviews")
OWNER_REVIEWS = "owner_reviews"


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    topic: str
    text: str
    applies_to: tuple[dict, ...]
    sources: tuple[dict, ...]
    checked: str

    def document(self) -> str:
        """The text embedded for semantic search: what the note says plus
        what it's about, so "is the 9070 XT power hungry" finds it."""
        return f"{self.title}. {self.text} Applies to: {describe_conditions(self.applies_to)}."


def _normalize(field: str, value, category: str) -> str:
    text = str(value)
    if field == "chip":
        return chip_key(text)
    if field == "model" and category == "cpu":
        return cpu_key(text)
    return " ".join(text.split()).lower()


def _condition_matches(condition: dict, part: dict) -> bool:
    category = condition.get("category")
    if category is None or part.get("category") != category:
        return False
    for field, expected in condition.items():
        if field == "category":
            continue
        if field == "model_pattern":
            if not re.search(expected, part.get("model") or ""):
                return False
            continue
        actual = part.get(field)
        if actual is None:
            return False
        accepted = expected if isinstance(expected, list) else [expected]
        if _normalize(field, actual, category) not in {_normalize(field, v, category) for v in accepted}:
            return False
    return True


def applies_to_part(note: Note, part: dict) -> bool:
    return any(_condition_matches(condition, part) for condition in note.applies_to)


def describe_conditions(conditions: tuple[dict, ...]) -> str:
    """"Arc A380, Arc A750 GPUs; AM5 motherboards", for the embedded text
    and for telling the model which parts a note covers."""
    described = []
    for condition in conditions:
        category = condition["category"]
        names = [", ".join(v) if isinstance(v, list) else str(v)
                 for k, v in condition.items() if k not in ("category", "model_pattern", "id")]
        if "id" in condition and not names:
            names.append("one catalog")
        if "model_pattern" in condition:
            names.append(_PATTERN_LABELS.get(condition["model_pattern"], "matching models"))
        described.append(f"{' '.join(names)} {_CATEGORY_LABELS.get(category, category)}".strip())
    return "; ".join(described)


_CATEGORY_LABELS = {"cpu": "CPUs", "gpu": "GPUs", "motherboard": "motherboards", "ram": "memory kits",
                    "storage": "drives", "psu": "power supplies", "cooler": "coolers", "case": "cases"}
# Readable names for the patterns in knowledge_notes.json; an unlisted
# pattern reads as "matching models".
_PATTERN_LABELS = {
    r"^Core i[3579]-1[34]\d{3}": "13th and 14th Gen Core",
    r"^Core Ultra [579] 2\d\d": "Core Ultra 200S",
}


def _parse(entry: dict) -> Note:
    if entry["topic"] not in TOPICS:
        raise ValueError(f"Note {entry['id']}: unknown topic {entry['topic']!r}")
    if not entry.get("sources"):
        raise ValueError(f"Note {entry['id']} has no source")
    return Note(
        id=entry["id"], title=entry["title"], topic=entry["topic"], text=entry["text"],
        applies_to=tuple(entry["applies_to"]), sources=tuple(entry["sources"]), checked=entry["checked"],
    )


@lru_cache
def load_notes(path: Path = REFERENCE, extra: tuple[Path, ...] = (OWNER_REVIEW_NOTES,)) -> tuple[Note, ...]:
    """Every note in `path`, plus those in any `extra` file that exists,
    validated. Raises ValueError on a duplicate ID, an unknown topic, or a
    note with no source."""
    entries = json.loads(path.read_text(encoding="utf-8"))["notes"]
    for extra_path in extra:
        if extra_path.exists():
            entries += json.loads(extra_path.read_text(encoding="utf-8"))["notes"]
    notes = tuple(_parse(entry) for entry in entries)
    ids = [note.id for note in notes]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate note IDs in " + str(path))
    return notes


def note_by_id(note_id: str) -> Note | None:
    return _notes_by_id().get(note_id)


@lru_cache
def _notes_by_id() -> dict[str, Note]:
    return {note.id: note for note in load_notes()}


def owner_review_note_id(part_id) -> str:
    """The ID review_notes gives a part's owner-review note."""
    return f"amazon-reviews-{part_id}"


def notes_for_parts(parts: list[dict], notes: tuple[Note, ...] | None = None) -> dict[str, list[dict]]:
    """Note ID -> the parts it applies to, for every note that applies to
    at least one of `parts`."""
    matched: dict[str, list[dict]] = {}
    for note in notes if notes is not None else load_notes():
        hits = [part for part in parts if applies_to_part(note, part)]
        if hits:
            matched[note.id] = hits
    return matched
