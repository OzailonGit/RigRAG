from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.product import Category
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.retrieval.hybrid import hybrid_search
from app.rag.vectorstore.qdrant_store import QdrantVectorStore

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    category: Category | None = None
    max_price_php: float | None = None
    manufacturer: str | None = None
    limit: int = 10


_store = QdrantVectorStore()


@router.post("")
def search(payload: SearchRequest, db: Session = Depends(get_db)) -> list[dict]:
    embedder = get_embedding_provider()
    return hybrid_search(
        db,
        embedder,
        _store,
        query=payload.query,
        category=payload.category,
        max_price_php=payload.max_price_php,
        manufacturer=payload.manufacturer,
        limit=payload.limit,
    )
