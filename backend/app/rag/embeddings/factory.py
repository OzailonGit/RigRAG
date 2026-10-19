from app.config import get_settings
from app.rag.embeddings.base import EmbeddingProvider


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.embedding_provider == "local":
        from app.rag.embeddings.local import get_local_embedding_provider

        return get_local_embedding_provider()
    if settings.embedding_provider == "cloud":
        from app.rag.embeddings.cloud import CloudEmbeddingProvider

        return CloudEmbeddingProvider(
            base_url=settings.custom_llm_base_url,
            api_key=settings.custom_llm_api_key,
            model=settings.embedding_model,
            dimension=settings.embedding_dim,
        )
    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")
