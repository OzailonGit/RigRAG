"""Runs the RAG evaluation set against the live Qdrant index.

Metrics:
- Hit@k: did any expected product appear in the top-k?
- MRR: how high did the first relevant hit rank?

Both are computed deterministically against the known seed catalog,
standing in for retrieval precision/recall without hand-labeled relevance
judgments.

Answer faithfulness and context relevance are not measured. An LLM judge
could score generated explanations, but it adds its own noise (it can be
wrong, inconsistent across runs, or biased toward verbose answers), so
this script checks only retrieval, which is objectively measurable.
"""

from app.database.session import SessionLocal
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.evaluation.eval_dataset import EVAL_CASES
from app.rag.retrieval.hybrid import hybrid_search
from app.rag.vectorstore.qdrant_store import QdrantVectorStore
from app.models.product import Category


def main() -> None:
    db = SessionLocal()
    embedder = get_embedding_provider()
    store = QdrantVectorStore()

    if store.count() == 0:
        print("Qdrant collection is empty. Run `python -m app.ingestion.embed_and_index` first.")
        return

    hits, reciprocal_ranks = 0, []
    for case in EVAL_CASES:
        results = hybrid_search(
            db, embedder, store,
            query=case.query,
            category=Category(case.category) if case.category else None,
            max_price_php=case.max_price_php,
            limit=case.k,
        )
        models = [r["model"] for r in results]
        rank = next((i + 1 for i, m in enumerate(models) if m in case.expected_models), None)

        if rank:
            hits += 1
            reciprocal_ranks.append(1 / rank)
        else:
            reciprocal_ranks.append(0.0)

        status = f"HIT (rank {rank})" if rank else "MISS"
        print(f"[{status}] \"{case.query}\"")
        print(f"    expected any of: {case.expected_models}")
        print(f"    got: {models}")

    n = len(EVAL_CASES)
    print(f"\nHit@k: {hits}/{n} ({hits / n:.0%})")
    print(f"MRR:   {sum(reciprocal_ranks) / n:.3f}")
    db.close()


if __name__ == "__main__":
    main()
