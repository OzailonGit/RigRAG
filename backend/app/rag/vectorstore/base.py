from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class VectorPoint:
    id: str
    vector: list[float]
    payload: dict[str, Any]


@dataclass
class VectorSearchResult:
    id: str
    score: float
    payload: dict[str, Any]


class VectorStore(ABC):
    """Abstraction over the vector database.

    Qdrant is the current implementation, but retrieval code depends only on
    this interface, so pgvector or another store can be swapped in without
    touching the RAG pipeline.
    """

    @abstractmethod
    def ensure_collection(self, dimension: int) -> None: ...

    @abstractmethod
    def recreate_collection(self, dimension: int) -> None:
        """Drops the collection, if any, and creates it empty."""

    def create_keyword_index(self, field: str) -> None:
        """Indexes a payload field for exact-match filtering. Optional: a
        store without payload indexes filters by scanning."""

    @abstractmethod
    def upsert(self, points: list[VectorPoint]) -> None: ...

    @abstractmethod
    def search(
        self,
        query_vector: list[float],
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[VectorSearchResult]: ...

    @abstractmethod
    def count(self) -> int: ...
