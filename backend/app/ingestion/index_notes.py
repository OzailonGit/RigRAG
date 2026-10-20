"""Embeds the knowledge notes (data/reference/knowledge_notes.json) into
their own Qdrant collection.

    python -m app.ingestion.index_notes

Rebuilds the collection from scratch, so a deleted or renamed note doesn't
linger. Run after editing the notes file; embed_and_index runs it too.
"""

from app.config import get_settings
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.knowledge.notes import load_notes
from app.rag.knowledge.retrieval import point_id
from app.rag.vectorstore.base import VectorPoint
from app.rag.vectorstore.qdrant_store import QdrantVectorStore


def main() -> None:
    notes = load_notes()
    embedder = get_embedding_provider()
    store = QdrantVectorStore(collection=get_settings().qdrant_notes_collection)
    store.recreate_collection(embedder.dimension)
    vectors = embedder.embed_documents([note.document() for note in notes])
    store.upsert([
        VectorPoint(id=point_id(note.id), vector=vector,
                    payload={"note_id": note.id, "topic": note.topic, "title": note.title})
        for note, vector in zip(notes, vectors)
    ])
    print(f"Indexed {store.count()} knowledge notes.")


if __name__ == "__main__":
    main()
