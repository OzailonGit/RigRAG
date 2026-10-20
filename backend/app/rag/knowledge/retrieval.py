"""Note retrieval: structured matching picks the notes that apply to the
parts in play, then semantic search ranks them against the question.

A note about the RX 9070 XT's power draw shouldn't reach an answer about an
RTX 5080, however similar the wording, so part matching (notes.py) is a
hard filter, like the category and price filters in hybrid part search.
Semantic search only orders what's left: for "is this build loud?", the
cooler's noise note before the GPU's PhysX note.

Without a reachable vector store (tests, or Qdrant down), notes are ranked
by shared words instead, so explanations degrade rather than fail.
"""

import logging
import re
import uuid
from dataclasses import dataclass

from app.config import get_settings
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.knowledge.notes import OWNER_REVIEWS, Note, load_notes, notes_for_parts
from app.rag.vectorstore.base import VectorStore

logger = logging.getLogger(__name__)


def point_id(note_id: str) -> str:
    """Qdrant point IDs must be UUIDs; derive a stable one from the note ID."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rigrag-note:{note_id}"))


@dataclass
class RetrievedNote:
    note: Note
    parts: list[str]  # "AMD Radeon RX 9070 XT ...", the parts in play the note applies to
    score: float


def _part_name(part: dict) -> str:
    return f"{part.get('manufacturer', '')} {part.get('model', '')}".strip()


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class NoteRetriever:
    def __init__(self, notes: tuple[Note, ...] | None = None, use_vectors: bool = True,
                 embedder: EmbeddingProvider | None = None, store: VectorStore | None = None):
        self._notes = notes
        self._use_vectors = use_vectors
        self._embedder = embedder
        self._store = store

    @property
    def notes(self) -> tuple[Note, ...]:
        return self._notes if self._notes is not None else load_notes()

    def for_parts(self, parts: list[dict], query: str, limit: int = 6, topics: set[str] | None = None,
                  exclude_topics: set[str] = frozenset()) -> list[RetrievedNote]:
        """The notes that apply to any of `parts`, most relevant to `query`
        first, optionally only those in `topics` or outside `exclude_topics`."""
        notes = tuple(n for n in self.notes
                      if (topics is None or n.topic in topics) and n.topic not in exclude_topics)
        matched = notes_for_parts(parts, notes)
        if not matched:
            return []
        by_id = {note.id: note for note in self.notes}
        candidates = [by_id[note_id] for note_id in matched]
        ranked = self._rank(query, candidates, limit)
        return [RetrievedNote(note, [_part_name(p) for p in matched[note.id]], score) for note, score in ranked]

    def search(self, query: str, limit: int = 4) -> list[RetrievedNote]:
        """The notes most relevant to `query`, whatever parts they cover.
        Owner-review notes are left out: each is about one product, and
        without the part in play it would read as being about any."""
        candidates = [note for note in self.notes if note.topic != OWNER_REVIEWS]
        return [RetrievedNote(note, [], score) for note, score in self._rank(query, candidates, limit)]

    def _rank(self, query: str, candidates: list[Note], limit: int) -> list[tuple[Note, float]]:
        if self._use_vectors:
            try:
                return self._rank_by_vector(query, candidates, limit)
            except Exception as exc:  # an unreachable store shouldn't break the answer
                logger.warning("Note search fell back to word overlap: %s", exc)
        return self._rank_by_words(query, candidates, limit)

    def _rank_by_vector(self, query: str, candidates: list[Note], limit: int) -> list[tuple[Note, float]]:
        if self._embedder is None:
            from app.rag.embeddings.factory import get_embedding_provider
            from app.rag.vectorstore.qdrant_store import QdrantVectorStore

            self._embedder = get_embedding_provider()
            self._store = QdrantVectorStore(collection=get_settings().qdrant_notes_collection)
        by_point = {point_id(note.id): note for note in candidates}
        hits = self._store.search(self._embedder.embed_query(query),
                                  filters={"note_id": {"in": [note.id for note in candidates]}}, limit=limit)
        ranked = [(by_point[hit.id], hit.score) for hit in hits if hit.id in by_point]
        if len(ranked) < min(limit, len(candidates)):
            # A note missing from the index (added since the last indexing run)
            # still applies to the part, so it goes after the ranked ones.
            seen = {note.id for note, _ in ranked}
            ranked += [(note, 0.0) for note in candidates if note.id not in seen][: limit - len(ranked)]
        return ranked

    @staticmethod
    def _rank_by_words(query: str, candidates: list[Note], limit: int) -> list[tuple[Note, float]]:
        query_words = _words(query)
        scored = [(note, len(query_words & _words(note.document())) / (len(query_words) or 1)) for note in candidates]
        # Stable sort: ties keep the file's order.
        return sorted(scored, key=lambda item: -item[1])[:limit]


_default: NoteRetriever | None = None


def get_note_retriever() -> NoteRetriever:
    global _default
    if _default is None:
        _default = NoteRetriever()
    return _default
