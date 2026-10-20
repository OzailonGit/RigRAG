"""Runs the retrieval evaluation set (eval_dataset.py) against the live
Qdrant index.

    python -m app.rag.evaluation.run_eval
    python -m app.rag.evaluation.run_eval --verbose    # every result, judged

Metrics, each averaged over the cases:
- Precision@k: the share of the top k results the case's rule accepts.
- Hit@k: whether any of the top k is relevant.
- MRR: 1 / the rank of the first relevant result (0 when there is none).

Answer faithfulness isn't measured. An LLM judge could score generated
explanations, but it adds its own noise (it can be wrong, inconsistent
across runs, or biased toward verbose answers), so this script checks only
retrieval, which the catalog's own specs can judge.
"""

import argparse
import time

from app.database.session import SessionLocal
from app.models.product import Category
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.evaluation.eval_dataset import EVAL_CASES
from app.rag.retrieval.hybrid import hybrid_search
from app.rag.vectorstore.qdrant_store import QdrantVectorStore


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", action="store_true", help="print every result with its judgment")
    args = parser.parse_args(argv)

    db = SessionLocal()
    embedder = get_embedding_provider()
    store = QdrantVectorStore()
    if store.count() == 0:
        print("Qdrant collection is empty. Run `python -m app.ingestion.embed_and_index` first.")
        return

    embedder.embed_query("warm-up")  # loads the model, so the first case isn't charged for it
    precisions, hits, reciprocal_ranks, latencies = [], [], [], []
    for case in EVAL_CASES:
        started = time.perf_counter()
        results = hybrid_search(
            db, embedder, store, query=case.query,
            category=Category(case.category) if case.category else None,
            max_price_php=case.max_price_php, manufacturer=case.manufacturer, limit=case.k,
        )
        latencies.append(time.perf_counter() - started)
        judged = [case.relevant(r) for r in results]
        relevant = sum(judged)
        rank = next((i + 1 for i, ok in enumerate(judged) if ok), None)

        precisions.append(relevant / case.k)
        hits.append(rank is not None)
        reciprocal_ranks.append(1 / rank if rank else 0.0)

        print(f"[{relevant}/{case.k}] \"{case.query}\"  (relevant: {case.rule})")
        if args.verbose or relevant < case.k:
            for result, ok in zip(results, judged):
                print(f"    {'+' if ok else '-'} {result['manufacturer']} {result['model']}")

    n = len(EVAL_CASES)
    latencies.sort()
    print(f"\n{n} cases")
    print(f"Precision@k: {sum(precisions) / n:.0%}")
    print(f"Hit@k:       {sum(hits)}/{n} ({sum(hits) / n:.0%})")
    print(f"MRR:         {sum(reciprocal_ranks) / n:.3f}")
    print(f"Latency:     median {latencies[n // 2] * 1000:.0f}ms, slowest {latencies[-1] * 1000:.0f}ms per query")
    db.close()


if __name__ == "__main__":
    main()
