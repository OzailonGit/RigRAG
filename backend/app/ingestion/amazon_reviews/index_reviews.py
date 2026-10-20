"""Splits the Amazon reviews of catalog parts into passages and indexes them
in Qdrant (the review_passages collection), for follow-up questions about
what owners experienced ("does the pump click at idle?").

    python -m app.ingestion.amazon_reviews.index_reviews            # rebuild the collection
    python -m app.ingestion.amazon_reviews.index_reviews --resume   # continue an interrupted run

Run after review_notes, which matches Amazon products to catalog parts.
Only parts with an owner-review note (MIN_REVIEWS or more reviews) get
passages, so every passage has a note to cite and a review count beside it.

Reviews under MIN_CHARS ("Works great!") are skipped: they rank well for
almost any query and say nothing a question needs. Each part keeps at most
MAX_PER_PART reviews, the most helpful first, then the longest, then the
newest; a part with 10,000 reviews would otherwise crowd the index without
changing what its passages say. A long review is split at sentence ends
into passages of up to MAX_PASSAGE_CHARS.

Passages hold review text, so like the rest of data/raw/amazon/ they stay
local (Qdrant's storage is gitignored) and are never shown verbatim: the
prompt has the model summarize them.
"""

import argparse
import hashlib
import json
import re
import time
import uuid
from collections import defaultdict

from app.config import get_settings
from app.ingestion.amazon_reviews.filter_components import OUTPUT_DIR
from app.ingestion.amazon_reviews.review_notes import REVIEW_NOTES
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.vectorstore.base import VectorPoint
from app.rag.vectorstore.qdrant_store import QdrantVectorStore

MIN_CHARS = 80
MAX_PER_PART = 400
MAX_PASSAGE_CHARS = 700
BATCH_SIZE = 256
UPSERT_ATTEMPTS = 3

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def passages(text: str, limit: int = MAX_PASSAGE_CHARS) -> list[str]:
    """Splits text at sentence ends into pieces of at most `limit`
    characters; a single longer sentence becomes its own piece."""
    pieces, current = [], ""
    for sentence in _SENTENCE_END.split(text.replace("\n", " ")):
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def review_text(review: dict) -> str:
    """The title adds information only when it isn't just the start of the text."""
    title, text = review["title"].strip(), review["text"].strip()
    if not title or text.lower().startswith(title.lower().rstrip(".!")):
        return text
    return f"{title.rstrip('.!')}. {text}"


def _rank_key(review: dict) -> tuple:
    return (-review["helpful_votes"], -min(len(review["text"]), 1500), review["date"])


def _point_id(parent_asin: str, review: dict, piece: int) -> str:
    digest = hashlib.sha1(f"{parent_asin}|{review['date']}|{review['text']}".encode()).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rigrag-review:{digest}:{piece}"))


def _upsert_with_retries(store: QdrantVectorStore, points: list[VectorPoint]) -> None:
    for attempt in range(1, UPSERT_ATTEMPTS + 1):
        try:
            store.upsert(points)
            return
        except Exception:  # timeouts surface as several qdrant_client exception types
            if attempt == UPSERT_ATTEMPTS:
                raise
            time.sleep(10 * attempt)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resume", action="store_true",
                        help="keep the collection and continue after the passages already indexed")
    args = parser.parse_args(argv)

    noted = {note["applies_to"][0]["id"] for note in json.loads(REVIEW_NOTES.read_text())["notes"]}
    asin_parts: dict[str, tuple[str, ...]] = {}
    with (OUTPUT_DIR / "matches.jsonl").open(encoding="utf-8") as matches:
        for line in matches:
            match = json.loads(line)
            ids = tuple(sorted(pid for pid in match["part_ids"] if pid in noted))
            if ids:
                asin_parts[match["parent_asin"]] = ids

    # Grouped by the parts a review belongs to (one part, or catalog
    # duplicates sharing it), so the cap applies per part.
    by_parts: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    with (OUTPUT_DIR / "reviews.jsonl").open(encoding="utf-8") as source:
        for line in source:
            review = json.loads(line)
            parts = asin_parts.get(review["parent_asin"])
            if parts and len(review["text"]) >= MIN_CHARS:
                by_parts[parts].append(review)

    points_payload = []
    for parts, reviews in by_parts.items():
        for review in sorted(reviews, key=_rank_key)[:MAX_PER_PART]:
            for piece, text in enumerate(passages(review_text(review))):
                points_payload.append((_point_id(review["parent_asin"], review, piece), {
                    "part_ids": list(parts),
                    "parent_asin": review["parent_asin"],
                    "rating": review["rating"],
                    "date": review["date"],
                    "verified_purchase": review["verified_purchase"],
                    "helpful_votes": review["helpful_votes"],
                    "text": text,
                }))
    print(f"{len(points_payload):,} passages from {sum(len(r) for r in by_parts.values()):,} reviews "
          f"of {len(by_parts):,} parts", flush=True)

    embedder = get_embedding_provider()
    store = QdrantVectorStore(collection=get_settings().qdrant_reviews_collection)
    first = 0
    if args.resume:
        # Passages come out in the same order every run and IDs are stable,
        # so redoing the last partial batch is harmless.
        first = store.count() // BATCH_SIZE * BATCH_SIZE
        print(f"Resuming at passage {first:,}", flush=True)
    else:
        store.recreate_collection(embedder.dimension)
        store.create_keyword_index("part_ids")
    for start in range(first, len(points_payload), BATCH_SIZE):
        batch = points_payload[start:start + BATCH_SIZE]
        vectors = embedder.embed_documents([payload["text"] for _, payload in batch])
        _upsert_with_retries(store, [VectorPoint(id=pid, vector=v, payload=payload)
                                     for (pid, payload), v in zip(batch, vectors)])
        if (start // BATCH_SIZE) % 40 == 0:
            print(f"  {start + len(batch):,}/{len(points_payload):,}", flush=True)
    print(f"Indexed {store.count():,} review passages.")


if __name__ == "__main__":
    main()
