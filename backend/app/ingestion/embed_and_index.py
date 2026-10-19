"""Ingestion pipeline stage: Structured DB rows -> documents -> embeddings -> Qdrant.

Raw Source -> Parser -> Normalizer -> Validator -> Structured Database (seed.py)
    -> Document Generator -> Embedding Model -> Vector Database (this file)

Run after app.ingestion.seed has populated Postgres.
"""

from app.database.session import SessionLocal
from app.models.product import Product
from app.rag.chunking.document_builder import build_product_document
from app.rag.chunking.payload_builder import build_payload
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.vectorstore.base import VectorPoint
from app.rag.vectorstore.qdrant_store import QdrantVectorStore
from app.services.parts_service import to_full_dict

BATCH_SIZE = 32


def main() -> None:
    db = SessionLocal()
    embedder = get_embedding_provider()
    store = QdrantVectorStore()
    store.ensure_collection(embedder.dimension)

    products = db.query(Product).all()
    print(f"Indexing {len(products)} products into Qdrant...")

    total = 0
    for i in range(0, len(products), BATCH_SIZE):
        batch = products[i : i + BATCH_SIZE]
        full_dicts = [to_full_dict(db, p) for p in batch]
        documents = [build_product_document(d) for d in full_dicts]
        vectors = embedder.embed_documents(documents)

        points = [
            VectorPoint(id=str(d["id"]), vector=vec, payload={**build_payload(d), "document": doc})
            for d, vec, doc in zip(full_dicts, vectors, documents)
        ]
        store.upsert(points)
        total += len(points)
        print(f"  upserted {total}/{len(products)}")

    print(f"Indexing complete. Collection now holds {store.count()} points.")
    db.close()


if __name__ == "__main__":
    main()
