"""Review passage retrieval: semantic search over Amazon owner reviews
(app.ingestion.amazon_reviews.index_reviews), filtered to the parts asked
about.

The part filter is hard, like note matching: a passage about one cooler's
pump never answers a question about another's. Semantic search ranks the
part's passages against the question, so "does the pump click?" returns
the reviews that talk about pump sounds, from among hundreds that don't.

Passages are the most similar reviews, not the matching ones: a part with
no pump complaints still returns its closest passages. The prompt has the
model count only passages that actually say what was asked.
"""

import logging
from dataclasses import dataclass

from app.config import get_settings
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.vectorstore.base import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class ReviewPassage:
    text: str
    rating: float | None
    date: str
    verified_purchase: bool
    helpful_votes: int
    score: float

    def for_prompt(self) -> dict:
        return {"rating": self.rating, "date": self.date[:7], "verified_purchase": self.verified_purchase,
                "text": self.text}


class ReviewUnavailable(Exception):
    """The review index can't be reached or hasn't been built."""


class ReviewRetriever:
    def __init__(self, embedder: EmbeddingProvider | None = None, store: VectorStore | None = None):
        self._embedder = embedder
        self._store = store

    def _connect(self) -> None:
        if self._embedder is None:
            from app.rag.embeddings.factory import get_embedding_provider
            from app.rag.vectorstore.qdrant_store import QdrantVectorStore

            self._embedder = get_embedding_provider()
            self._store = QdrantVectorStore(collection=get_settings().qdrant_reviews_collection)

    def search(self, query: str, part_ids: list[str], limit: int = 8) -> list[ReviewPassage]:
        """The passages from the given parts' reviews most similar to `query`.
        Raises ReviewUnavailable when the index can't be searched."""
        try:
            self._connect()
            hits = self._store.search(self._embedder.embed_query(query),
                                      filters={"part_ids": {"in": [str(pid) for pid in part_ids]}}, limit=limit)
        except Exception as exc:
            logger.warning("Review passage search failed: %s", exc)
            raise ReviewUnavailable(str(exc)) from exc
        return [ReviewPassage(text=h.payload["text"], rating=h.payload.get("rating"), date=h.payload.get("date", ""),
                              verified_purchase=bool(h.payload.get("verified_purchase")),
                              helpful_votes=h.payload.get("helpful_votes") or 0, score=h.score) for h in hits]


_default: ReviewRetriever | None = None


def get_review_retriever() -> ReviewRetriever:
    global _default
    if _default is None:
        _default = ReviewRetriever()
    return _default
