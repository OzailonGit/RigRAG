from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Abstraction over how text is turned into vectors.

    The RAG pipeline and vector store depend only on this interface, never on
    sentence-transformers or a specific API, so LocalEmbeddingProvider and
    CloudEmbeddingProvider are interchangeable.
    """

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of product documents for indexing."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a single user query for retrieval."""
