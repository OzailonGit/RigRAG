from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import get_settings
from app.rag.vectorstore.base import VectorPoint, VectorSearchResult, VectorStore


def _build_filter(filters: dict[str, Any] | None) -> qmodels.Filter | None:
    """Translate a plain filter dict into a Qdrant Filter.

    Supported per-field shapes:
      "category": "gpu"                 -> exact match
      "manufacturer": {"in": [...]}      -> match any
      "price_php": {"lte": 25000}        -> range (gte/lte/gt/lt)
    """
    if not filters:
        return None

    must: list[qmodels.Condition] = []
    for field, value in filters.items():
        if isinstance(value, dict):
            range_kwargs = {k: v for k, v in value.items() if k in ("gte", "lte", "gt", "lt")}
            if range_kwargs:
                must.append(qmodels.FieldCondition(key=field, range=qmodels.Range(**range_kwargs)))
            elif "in" in value:
                must.append(qmodels.FieldCondition(key=field, match=qmodels.MatchAny(any=value["in"])))
        else:
            must.append(qmodels.FieldCondition(key=field, match=qmodels.MatchValue(value=value)))

    return qmodels.Filter(must=must) if must else None


class QdrantVectorStore(VectorStore):
    def __init__(self, host: str | None = None, port: int | None = None, collection: str | None = None):
        settings = get_settings()
        self._client = QdrantClient(host=host or settings.qdrant_host, port=port or settings.qdrant_port,
                                    timeout=settings.qdrant_timeout_s)
        self._collection = collection or settings.qdrant_collection

    def ensure_collection(self, dimension: int) -> None:
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection in existing:
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qmodels.VectorParams(size=dimension, distance=qmodels.Distance.COSINE),
        )

    def recreate_collection(self, dimension: int) -> None:
        if self._client.collection_exists(self._collection):
            self._client.delete_collection(self._collection)
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qmodels.VectorParams(size=dimension, distance=qmodels.Distance.COSINE),
        )

    def create_keyword_index(self, field: str) -> None:
        self._client.create_payload_index(self._collection, field_name=field,
                                          field_schema=qmodels.PayloadSchemaType.KEYWORD)

    def upsert(self, points: list[VectorPoint]) -> None:
        self._client.upsert(
            collection_name=self._collection,
            points=[
                qmodels.PointStruct(id=p.id, vector=p.vector, payload=p.payload)
                for p in points
            ],
        )

    def search(
        self,
        query_vector: list[float],
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[VectorSearchResult]:
        results = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            query_filter=_build_filter(filters),
            limit=limit,
        ).points
        return [VectorSearchResult(id=str(r.id), score=r.score, payload=r.payload or {}) for r in results]

    def count(self) -> int:
        return self._client.count(collection_name=self._collection).count
