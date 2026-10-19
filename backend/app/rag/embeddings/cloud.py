import httpx

from app.rag.embeddings.base import EmbeddingProvider


class CloudEmbeddingProvider(EmbeddingProvider):
    """Calls an OpenAI-compatible /embeddings endpoint.

    Fallback for machines without the hardware or bandwidth to run a local
    embedding model. LocalEmbeddingProvider remains the default.
    """

    def __init__(self, base_url: str, api_key: str, model: str, dimension: int):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "input": texts},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()["data"]
        return [item["embedding"] for item in data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]
