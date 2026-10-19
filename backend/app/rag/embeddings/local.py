from functools import lru_cache

from app.config import get_settings
from app.rag.embeddings.base import EmbeddingProvider


class LocalEmbeddingProvider(EmbeddingProvider):
    """Runs embeddings locally via sentence-transformers.

    Default model is BAAI/bge-small-en-v1.5: small, fast on CPU, MIT-licensed,
    and a standard BERT architecture with no custom remote code.
    nomic-embed-text was dropped because its HF-hub model code calls
    `PreTrainedModel.get_extended_attention_mask`, which current transformers
    has removed.

    BGE is asymmetric: queries need an instruction prefix, documents are
    embedded as-is.
    """

    QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str | None = None):
        from sentence_transformers import SentenceTransformer  # deferred: heavy import

        settings = get_settings()
        self._model_name = model_name or settings.embedding_model
        self._model = SentenceTransformer(self._model_name)
        self._dim = self._model.get_sentence_embedding_dimension()

    @property
    def dimension(self) -> int:
        return self._dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode(f"{self.QUERY_INSTRUCTION}{text}", normalize_embeddings=True).tolist()


@lru_cache
def get_local_embedding_provider() -> LocalEmbeddingProvider:
    return LocalEmbeddingProvider()
