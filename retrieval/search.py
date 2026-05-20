from __future__ import annotations

import math
import logging
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, GeoBoundingBox, GeoPoint, NamedSparseVector
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi

from retrieval.bm25_index import query_sparse_vector

logger = logging.getLogger(__name__)

_RRF_K = 60  # standard constant — prevents rank-1 from dominating


def _rrf_score(rank: int) -> float:
    # WHY: rank is 0-based here; formula uses rank+1 so rank-0 gives 1/(1+60)=0.016
    return 1.0 / (rank + 1 + _RRF_K)


def _build_geo_filter(lat: float, lng: float, radius_km: float) -> Filter:
    # WHY: longitude degrees are shorter than latitude degrees at non-equatorial latitudes.
    # At Delhi (~28.6°N): 1° lng ≈ 97.4 km. Using a fixed 111 divisor for both
    # would make the east-west radius ~12% too wide.
    lat_delta = radius_km / 111.0
    lng_delta = radius_km / (111.0 * math.cos(math.radians(lat)))

    return Filter(
        must=[
            FieldCondition(
                key="lat",
                range={"gte": lat - lat_delta, "lte": lat + lat_delta},
            ),
            FieldCondition(
                key="lng",
                range={"gte": lng - lng_delta, "lte": lng + lng_delta},
            ),
        ]
    )


def hybrid_search(
    client: QdrantClient,
    embed_model: SentenceTransformer,
    bm25: BM25Okapi,
    query_text: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius_km: float = 2.0,
    top_k: int = 10,
    collection_name: str = "delhi_crimes",
) -> list[dict]:
    """
    Hybrid search combining dense (semantic) + sparse (BM25) vectors via RRF fusion.

    If lat/lng are provided, a geo bounding-box filter is applied before both
    dense and sparse searches — this is Feature A (route evidence retrieval).
    If lat/lng are None, the search is text-only — this is Feature D (semantic search).

    Returns a list of dicts (top_k results), each containing all payload fields
    plus rrf_score and match_type ("dense", "sparse", or "both").
    """
    geo_filter = _build_geo_filter(lat, lng, radius_km) if lat is not None else None

    # WHY: fetch top_k * 2 from each list before fusing — RRF needs enough candidates
    # from both sides so the fusion has room to re-rank correctly
    fetch_k = top_k * 2

    # --- Dense search ---
    query_vector = embed_model.encode(query_text, normalize_embeddings=True).tolist()
    dense_results = client.search(
        collection_name=collection_name,
        query_vector=("dense", query_vector),
        query_filter=geo_filter,
        limit=fetch_k,
        with_payload=True,
    )

    # --- Sparse (BM25) search ---
    # WHY NamedSparseVector not tuple shorthand: the tuple ("name", vec) only works
    # for dense float lists. SparseVector has .indices/.values — qdrant-client needs
    # the explicit NamedSparseVector wrapper to route it to the sparse index.
    sparse_vec = query_sparse_vector(bm25, query_text)
    sparse_results = client.search(
        collection_name=collection_name,
        query_vector=NamedSparseVector(name="sparse", vector=sparse_vec),
        query_filter=geo_filter,
        limit=fetch_k,
        with_payload=True,
    )

    # --- RRF fusion ---
    # Accumulate scores keyed by point id
    scores: dict[int, float] = {}
    match_type: dict[int, str] = {}
    payloads: dict[int, dict] = {}

    for rank, hit in enumerate(dense_results):
        pid = hit.id
        scores[pid] = scores.get(pid, 0.0) + _rrf_score(rank)
        match_type[pid] = "dense"
        payloads[pid] = hit.payload or {}

    for rank, hit in enumerate(sparse_results):
        pid = hit.id
        scores[pid] = scores.get(pid, 0.0) + _rrf_score(rank)
        if pid in match_type:
            match_type[pid] = "both"
        else:
            match_type[pid] = "sparse"
        payloads[pid] = hit.payload or {}

    # Sort by fused RRF score descending, return top_k
    ranked_ids = sorted(scores, key=lambda pid: scores[pid], reverse=True)[:top_k]

    return [
        {
            **payloads[pid],
            "rrf_score": round(scores[pid], 6),
            "match_type": match_type[pid],
        }
        for pid in ranked_ids
    ]
