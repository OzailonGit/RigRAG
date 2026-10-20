"""Hybrid retrieval: structured filters narrow the candidate pool, semantic
search ranks within it.

Pure vector similarity can violate hard constraints. For "good 1440p GPU
under 25000 PHP", a GPU at 40000 PHP can still be the closest embedding.
Metadata filtering keeps hard constraints exact and leaves semantic search
for the fuzzy part of the query (what "good for 1440p" means).

The vector payload is a cache, not the source of truth. After scoring, the
current row is re-fetched from Postgres so returned prices and specs are
never stale.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.product import Category
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.vectorstore.base import VectorStore
from app.services import parts_service


def build_qdrant_filters(
    category: Category | None = None,
    max_price_php: float | None = None,
    manufacturer: str | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if category is not None:
        filters["category"] = category.value
    if max_price_php is not None:
        filters["price_php"] = {"lte": max_price_php}
    if manufacturer is not None:
        filters["brand_keys"] = manufacturer.lower()  # see payload_builder.build_payload
    return filters


def hybrid_search(
    db: Session,
    embedder: EmbeddingProvider,
    store: VectorStore,
    query: str,
    category: Category | None = None,
    max_price_php: float | None = None,
    manufacturer: str | None = None,
    limit: int = 10,
) -> list[dict]:
    query_vector = embedder.embed_query(query)
    filters = build_qdrant_filters(category, max_price_php, manufacturer)
    hits = store.search(query_vector, filters=filters, limit=limit)

    results = []
    for hit in hits:
        product = parts_service.get_part(db, uuid.UUID(hit.id))
        if product is None:
            continue  # stale vector entry for a deleted product; skip rather than serve ghost data
        results.append({**product, "relevance_score": hit.score})
    return results
