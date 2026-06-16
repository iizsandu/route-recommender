"""
Rebuild retrieval/bm25_model.pkl from the EXISTING Qdrant index — no MongoDB.

WHY this works: BM25's sparse dimension here is the document-index space
(bm25_index.corpus_sparse_vector scores every corpus document), and the
pipeline stored each crime as Qdrant point id = corpus position. Refitting BM25
on the same corpus, in point-id order, reproduces the model whose sparse vectors
are already baked into the collection. The augmented text per document is fully
reconstructable from the stored payload (crime_type. location_exact. victim.
weapon_used. summary) — an exact replica of retrieval/pipeline.py.

Use when the original bm25_model.pkl is absent locally (it is gitignored and
only baked into the production image) and MongoDB is unavailable, so query-time
hybrid search would otherwise be disabled.

Usage:
    python scripts/rebuild_bm25_from_qdrant.py
    python scripts/rebuild_bm25_from_qdrant.py --host localhost --port 6333
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qdrant_client import QdrantClient

from retrieval import bm25_index

_COLLECTION = "delhi_crimes"
_OUT = Path(__file__).resolve().parents[1] / "retrieval" / "bm25_model.pkl"


def _aug_text(p: dict) -> str:
    """EXACT replica of retrieval/pipeline.py aug_texts construction."""
    parts: list[str] = []
    if p.get("crime_type"):
        parts.append(str(p["crime_type"]))
    if p.get("location_exact"):
        parts.append(str(p["location_exact"]))
    if p.get("victim"):
        parts.append(str(p["victim"]))
    if p.get("weapon_used"):
        parts.append(str(p["weapon_used"]))
    # pipeline always appends the summary last (distilbart output, never None).
    parts.append(str(p.get("summary") or ""))
    return ". ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=6333)
    args = ap.parse_args()

    client = QdrantClient(host=args.host, port=args.port, timeout=30)
    total = client.count(_COLLECTION).count
    print(f"collection '{_COLLECTION}' has {total} points; scrolling payloads ...")

    payloads: dict[int, dict] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=_COLLECTION,
            with_payload=True,
            with_vectors=False,
            limit=1000,
            offset=offset,
        )
        for pt in points:
            payloads[int(pt.id)] = pt.payload or {}
        if offset is None:
            break

    ids = sorted(payloads.keys())
    expected = list(range(ids[-1] + 1)) if ids else []
    if ids != expected:
        print(
            f"WARNING: point ids are not a contiguous 0..{ids[-1] if ids else -1} range "
            f"({len(ids)} ids present). Sparse-vector alignment may be approximate; "
            "dense + RRF will still function."
        )

    # Reconstruct the corpus in point-id order so document position == point id,
    # matching how the original pipeline enumerated and stored the records.
    corpus = [_aug_text(payloads[i]) for i in ids]
    print(f"reconstructed {len(corpus)} augmented texts; fitting BM25 ...")

    bm25 = bm25_index.fit(corpus)
    bm25_index.save(bm25, _OUT)
    print(f"saved BM25 model -> {_OUT}")
    print("Restart the backend so retrieval_service.init() picks it up.")


if __name__ == "__main__":
    main()
