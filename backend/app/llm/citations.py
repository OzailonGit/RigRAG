"""Numbered citations for knowledge notes.

Notes reach the model numbered ("ref": 1, 2, ...), and the reply cites them
as [1] or [1, 3] after the sentence that uses them. After the reply comes
back, citations are normalized (some models write 【1】), citations to
numbers that were never served are removed (a model can invent them), and
the cited notes become the reply's sources list.
"""

import re
from dataclasses import dataclass, field

from app.rag.knowledge.notes import describe_conditions
from app.rag.knowledge.retrieval import RetrievedNote

# [2] or [1, 3], but not a markdown link's text: "[1](https://...)".
CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\](?!\()")


@dataclass
class NoteSet:
    """The notes served to the model during one turn, numbered in the order
    they were first served."""

    served: list[RetrievedNote] = field(default_factory=list)
    # Citations removed for pointing at a number never served; the note
    # evaluation reports them.
    unserved_citations: int = 0

    def add(self, retrieved: RetrievedNote) -> int:
        for ref, existing in enumerate(self.served, start=1):
            if existing.note.id == retrieved.note.id:
                for part in retrieved.parts:
                    if part not in existing.parts:
                        existing.parts.append(part)
                return ref
        self.served.append(retrieved)
        return len(self.served)

    def add_all(self, notes: list[RetrievedNote]) -> list[dict]:
        """Serves `notes` and returns them as the model sees them."""
        return [self.for_prompt(self.add(note)) for note in notes]

    def for_prompt(self, ref: int) -> dict:
        retrieved = self.served[ref - 1]
        note = retrieved.note
        return {
            "ref": ref,
            "topic": note.topic,
            "title": note.title,
            "text": note.text,
            "applies_to": retrieved.parts or describe_conditions(note.applies_to),
            "source": ", ".join(s["publisher"] for s in note.sources),
        }

    def prompt_notes(self) -> list[dict]:
        return [self.for_prompt(ref) for ref in range(1, len(self.served) + 1)]

    def by_ref(self, ref: int) -> RetrievedNote | None:
        return self.served[ref - 1] if 1 <= ref <= len(self.served) else None


def cited_refs(reply: str) -> list[int]:
    """Every number the reply cites, in order of first citation."""
    refs: list[int] = []
    for match in CITATION.finditer(reply):
        for ref in (int(n) for n in match.group(1).split(",")):
            if ref not in refs:
                refs.append(ref)
    return refs


_SPACED_CITATION = re.compile(r"( *)" + CITATION.pattern)
_WIDE_CITATION = re.compile(r"[【［]\s*(\d+(?:\s*[,，、]\s*\d+)*)\s*[】］]")


def normalize_citations(reply: str) -> str:
    """【1】 and ［1、2］ -> [1] and [1, 2]."""
    return _WIDE_CITATION.sub(lambda m: "[" + ", ".join(re.findall(r"\d+", m.group(1))) + "]", reply)


def drop_unserved_citations(reply: str, notes: NoteSet) -> str:
    """Normalizes citations and removes those to numbers that weren't served
    this turn, with the space before them."""
    def keep_served(match: re.Match) -> str:
        refs = [n.strip() for n in match.group(2).split(",")]
        kept = [n for n in refs if notes.by_ref(int(n))]
        notes.unserved_citations += len(refs) - len(kept)
        return f"{match.group(1)}[{', '.join(kept)}]" if kept else ""

    return _SPACED_CITATION.sub(keep_served, normalize_citations(reply))


def sources(reply: str, notes: NoteSet) -> list[dict]:
    """The notes the reply cites, with their sources, for the API response."""
    cited = []
    for ref in cited_refs(reply):
        retrieved = notes.by_ref(ref)
        if retrieved is not None:
            note = retrieved.note
            cited.append({"ref": ref, "note_id": note.id, "title": note.title, "checked": note.checked,
                          "sources": [dict(s) for s in note.sources]})
    return cited


def sources_markdown(cited: list[dict]) -> str:
    """"Sources" list appended under a reply, one line per cited note."""
    if not cited:
        return ""
    lines = []
    for entry in cited:
        links = ", ".join(f"[{s['publisher']}]({s['url']})" for s in entry["sources"])
        lines.append(f"[{entry['ref']}] {entry['title']}: {links}")
    return "\n\n**Sources**\n\n" + "\n\n".join(lines)
