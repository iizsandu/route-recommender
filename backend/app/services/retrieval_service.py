"""
Retrieval service — wraps retrieval/search.py for the backend serving path.

Graceful degradation: if QDRANT_HOST is not set or Qdrant is unreachable,
all public functions return empty lists. The /routes/recommend endpoint
still works; nearby_incidents fields are just empty.

Retrieval is local-only for now. To enable:
  1. Run: docker compose up qdrant
  2. Run: python scripts/build_index.py --rebuild  (one-time index build)
  3. Set QDRANT_HOST=localhost in .env
  4. pip install -r retrieval/requirements.txt (in the local backend venv)
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.utils.logger import get_logger

logger = get_logger(__name__)


class _HttpxQdrantClient:
    """
    Thin httpx-based wrapper that replaces QdrantClient for the search path.

    WHY: qdrant_client 1.9.1 uses httpcore's sync SSL backend which fails with
    [SSL: UNEXPECTED_EOF_WHILE_READING] from Docker Desktop on Windows. Plain
    httpx.Client works fine for the same endpoint. This wrapper exposes only the
    two methods that hybrid_search() calls (search + query) as direct REST POST
    calls, bypassing qdrant_client's httpcore transport entirely.
    """

    def __init__(self, url: str, api_key: str, timeout: int = 10) -> None:
        import httpx
        self._base_url = url.rstrip("/")
        self._client = httpx.Client(
            headers={"api-key": api_key} if api_key else {},
            timeout=timeout,
        )

    def _filter_to_dict(self, f) -> dict | None:
        """Convert a qdrant_client Filter object to plain JSON dict."""
        if f is None:
            return None
        from qdrant_client.models import FieldCondition
        must = []
        for cond in (f.must or []):
            if isinstance(cond, FieldCondition):
                if cond.range is not None:
                    r = cond.range
                    rng = {}
                    if r.gte is not None:
                        rng["gte"] = r.gte
                    if r.lte is not None:
                        rng["lte"] = r.lte
                    must.append({"key": cond.key, "range": rng})
                elif cond.match is not None:
                    m = cond.match
                    if hasattr(m, "any"):
                        must.append({"key": cond.key, "match": {"any": m.any}})
                    else:
                        must.append({"key": cond.key, "match": {"value": m.value}})
        return {"must": must} if must else None

    def _vec_payload(self, query_vector) -> dict:
        """Convert qdrant_client vector arg to REST payload field."""
        from qdrant_client.models import NamedSparseVector
        if isinstance(query_vector, tuple):
            # ("dense", [float, ...])
            return {"name": query_vector[0], "vector": query_vector[1]}
        if isinstance(query_vector, NamedSparseVector):
            sv = query_vector.vector
            return {"name": query_vector.name, "vector": {"indices": sv.indices, "values": sv.values}}
        # plain list
        return {"vector": query_vector}

    def search(self, collection_name: str, query_vector, query_filter=None, limit: int = 10, with_payload: bool = True):
        """Replicate QdrantClient.search() via direct REST POST."""
        payload: dict = {
            "vector": self._vec_payload(query_vector),
            "limit": limit,
            "with_payload": with_payload,
        }
        flt = self._filter_to_dict(query_filter)
        if flt:
            payload["filter"] = flt

        resp = self._client.post(
            f"{self._base_url}/collections/{collection_name}/points/search",
            json=payload,
        )
        resp.raise_for_status()
        result = resp.json().get("result", [])
        return [_Hit(r["id"], r.get("payload") or {}) for r in result]

    def scroll(self, collection_name: str, scroll_filter=None, limit: int = 500,
               offset=None, with_payload: bool = True, with_vectors: bool = False):
        """Replicate QdrantClient.scroll() via direct REST POST.
        Returns (list[_Hit], next_page_offset | None) matching the qdrant_client tuple API.
        """
        payload: dict = {"limit": limit, "with_payload": with_payload, "with_vectors": with_vectors}
        if offset is not None:
            payload["offset"] = offset
        flt = self._filter_to_dict(scroll_filter)
        if flt:
            payload["filter"] = flt

        resp = self._client.post(
            f"{self._base_url}/collections/{collection_name}/points/scroll",
            json=payload,
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        points = [_Hit(r["id"], r.get("payload") or {}) for r in result.get("points", [])]
        return points, result.get("next_page_offset")

    def close(self) -> None:
        self._client.close()


class _Hit:
    """Minimal stand-in for qdrant_client ScoredPoint."""
    __slots__ = ("id", "payload")

    def __init__(self, id_, payload):
        self.id = id_
        self.payload = payload

# WHY: retrieval/ lives at repo root, not inside backend/. Insert repo root so
# `from retrieval.search import hybrid_search` resolves correctly.
_REPO_ROOT = Path(__file__).resolve().parents[3]  # backend/app/services/ → repo root
sys.path.insert(0, str(_REPO_ROOT))

# ── Module-level singletons ────────────────────────────────────────────────
_client = None       # _HttpxQdrantClient — None if Qdrant unavailable
_embed_model = None  # SentenceTransformer — None if sentence-transformers not installed
_bm25 = None         # BM25Okapi — None if bm25_model.pkl not found
_ready = False       # True only when all three are loaded

# Saved at init time so _reconnect() can recreate the client without re-init.
_qdrant_url: str = ""
_qdrant_api_key: str = ""
_qdrant_host: str = ""
_qdrant_port: int = 6333

# Constant query text for Feature A (route evidence retrieval).
# WHY constant: the primary filter is geo, not text. This query surfaces
# female-safety-relevant crime types from within the geo radius.
_SAFETY_QUERY = "crime robbery assault kidnapping safety woman female"

# WHY 500m: 100m (KDE scoring interval) → 20+ Qdrant queries per route with
# heavy overlap (2km radius). 500m → 5-8 queries, minimal overlap, 4× faster.
_SAMPLE_INTERVAL_M = 500.0


def _reconnect() -> bool:
    """Recreate the _HttpxQdrantClient to flush stale httpx connection pool."""
    global _client
    if not _qdrant_url and not _qdrant_host:
        return False
    try:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        if _qdrant_url:
            _client = _HttpxQdrantClient(url=_qdrant_url, api_key=_qdrant_api_key, timeout=5)
        else:
            _client = _HttpxQdrantClient(
                url=f"http://{_qdrant_host}:{_qdrant_port}",
                api_key=_qdrant_api_key,
                timeout=5,
            )
        logger.info("Qdrant client reconnected")
        return True
    except Exception as exc:
        logger.warning("Qdrant reconnect failed: %s", exc)
        return False


def init(
    qdrant_host: str,
    qdrant_port: int,
    bm25_model_path: Path,
    qdrant_url: str = "",
    qdrant_api_key: str = "",
) -> bool:
    """
    Initialise the retrieval service. Called once from lifespan startup.
    Returns True if all components loaded successfully.
    qdrant_url takes precedence over qdrant_host/qdrant_port when set (cloud mode).
    """
    global _client, _embed_model, _bm25, _ready
    global _qdrant_url, _qdrant_api_key, _qdrant_host, _qdrant_port

    if not qdrant_url and not qdrant_host:
        logger.info("QDRANT_HOST/QDRANT_URL not set — retrieval features disabled (graceful)")
        return False

    # Save connection params so _reconnect() can recreate the client later.
    _qdrant_url = qdrant_url
    _qdrant_api_key = qdrant_api_key
    _qdrant_host = qdrant_host
    _qdrant_port = qdrant_port

    # Create httpx-based Qdrant client (bypasses qdrant_client's httpcore SSL issues).
    # WHY _HttpxQdrantClient not QdrantClient: qdrant_client 1.9.1 uses httpcore's sync
    # SSL backend which fails with UNEXPECTED_EOF_WHILE_READING from Docker Desktop on
    # Windows. Plain httpx works for the same endpoint. _HttpxQdrantClient implements
    # only the search() method that hybrid_search() calls.
    if qdrant_url:
        _client = _HttpxQdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=5)
        logger.info("Qdrant Cloud client created (%s)", qdrant_url)
    else:
        _client = _HttpxQdrantClient(
            url=f"http://{qdrant_host}:{qdrant_port}",
            api_key=qdrant_api_key,
            timeout=5,
        )
        logger.info("Qdrant client created (%s:%d)", qdrant_host, qdrant_port)

    # Health check via plain httpx GET /collections — non-fatal if it fails.
    try:
        import httpx as _httpx
        hdr = {"api-key": qdrant_api_key} if qdrant_api_key else {}
        base = qdrant_url or f"http://{qdrant_host}:{qdrant_port}"
        r = _httpx.get(f"{base.rstrip('/')}/collections", headers=hdr, timeout=5)
        r.raise_for_status()
        n = len(r.json().get("result", {}).get("collections", []))
        logger.info("Qdrant health check passed (%d collections)", n)
    except Exception as exc:
        logger.warning("Qdrant health check failed (%s) — search will retry on first call", exc)

    # Load bge-small embedding model
    try:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer("BAAI/bge-small-en-v1.5")
        logger.info("bge-small embedding model loaded")
    except ImportError:
        logger.warning(
            "sentence-transformers not installed — run: pip install -r retrieval/requirements.txt"
        )
        _client = None
        return False

    # Load BM25 model from disk
    try:
        from retrieval.bm25_index import load as bm25_load
        _bm25 = bm25_load(bm25_model_path)
        logger.info("BM25 model loaded from %s", bm25_model_path)
    except FileNotFoundError:
        logger.warning(
            "BM25 model not found at %s — run: python scripts/build_index.py --rebuild",
            bm25_model_path,
        )
        _client = None
        return False

    _ready = True
    logger.info("Retrieval service ready")
    return True


def get_nearby_incidents(
    lat: float,
    lng: float,
    radius_km: float = 2.0,
    top_k: int = 5,
    allowed_crime_types: list[str] | None = None,
) -> list[dict]:
    """
    Return the top_k historically reported incidents within radius_km of (lat, lng).
    If allowed_crime_types is provided, only incidents matching those crime_macro
    values are returned. Returns [] if retrieval service is not ready.
    """
    if not _ready:
        return []

    from retrieval.search import hybrid_search

    def _do_search() -> list[dict]:
        return hybrid_search(
            client=_client,
            embed_model=_embed_model,
            bm25=_bm25,
            query_text=_SAFETY_QUERY,
            lat=lat,
            lng=lng,
            radius_km=radius_km,
            top_k=top_k,
            allowed_crime_types=allowed_crime_types,
        )

    try:
        return _do_search()
    except Exception as exc:
        # WHY reconnect: stale HTTP connection pool after idle periods causes
        # ResponseHandlingException/SSL_EOF. A fresh client opens a new pool.
        logger.warning(
            "Qdrant search failed for (%.4f, %.4f): %s — reconnecting and retrying",
            lat, lng, exc,
        )
        if _reconnect():
            try:
                return _do_search()
            except Exception as retry_exc:
                logger.error(
                    "Qdrant retry failed for (%.4f, %.4f): %s", lat, lng, retry_exc
                )
        return []


# Female-safety-relevant crime categories for route incident filtering.
# WHY these five: they represent crimes with direct physical threat to the
# target user (female commuter). Fraud, drug possession, and terrorism/riot
# are excluded — they don't inform safe routing decisions at the street level.
# Strings must exactly match map_crime_macro() return values in
# ml/data/category_mapping.py (verified: all five are in MACRO_PRIORITY).
_FEMALE_SAFETY_CATEGORIES = [
    "Sexual Violence",
    "Robbery",
    "Assault",
    "Kidnapping",
    "Murder",
]


def get_route_incidents(
    waypoints: list[tuple[float, float]],
    radius_km: float = 2.0,
    top_k_per_point: int = 3,
    max_total: int = 5,
) -> list[dict]:
    """
    Sample waypoints along a route and retrieve nearby incidents.
    Only female-safety-relevant crime categories are returned (see
    _FEMALE_SAFETY_CATEGORIES). Deduplicates by URL so the same news
    article doesn't appear twice. Returns at most max_total incidents.
    """
    if not _ready or not waypoints:
        return []

    # Sample waypoints every _SAMPLE_INTERVAL_M metres along the route.
    # WHY not all waypoints: 100m interval → 20+ Qdrant calls with heavy
    # overlap (2km radius). 500m interval → 5-8 calls, same coverage area.
    sampled = _sample_waypoints(waypoints, _SAMPLE_INTERVAL_M)

    seen_urls: set[str] = set()
    results: list[dict] = []

    for lat, lng in sampled:
        if len(results) >= max_total:
            break
        hits = get_nearby_incidents(
            lat, lng,
            radius_km=radius_km,
            top_k=top_k_per_point,
            allowed_crime_types=_FEMALE_SAFETY_CATEGORIES,
        )
        for hit in hits:
            url = hit.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(hit)
                if len(results) >= max_total:
                    break

    return results


def get_personalised_incidents(
    situation_text: str,
    waypoints: list[tuple[float, float]],
    radius_km: float = 2.0,
    max_total: int = 8,
) -> list[dict]:
    """
    Retrieve incidents relevant to a specific situation description along a route.
    Unlike get_route_incidents(), uses situation_text as the embedding query instead
    of the fixed safety query — so results are ranked by semantic match to the
    user's described context.
    Deduplicates by URL, collects across all sampled waypoints, then sorts by
    rrf_score descending before truncating to max_total.
    """
    if not _ready or not waypoints:
        return []

    sampled = _sample_waypoints(waypoints, _SAMPLE_INTERVAL_M)

    seen_urls: set[str] = set()
    results: list[dict] = []
    _reconnected_this_call = False

    from retrieval.search import hybrid_search

    for lat, lng in sampled:
        try:
            hits = hybrid_search(
                client=_client,
                embed_model=_embed_model,
                bm25=_bm25,
                query_text=situation_text,
                lat=lat,
                lng=lng,
                radius_km=radius_km,
                top_k=5,
                allowed_crime_types=_FEMALE_SAFETY_CATEGORIES,
            )
        except Exception as exc:
            if not _reconnected_this_call:
                logger.warning("personalised search failed (%.4f, %.4f): %s — reconnecting", lat, lng, exc)
                _reconnected_this_call = True
                if _reconnect():
                    try:
                        hits = hybrid_search(
                            client=_client,
                            embed_model=_embed_model,
                            bm25=_bm25,
                            query_text=situation_text,
                            lat=lat,
                            lng=lng,
                            radius_km=radius_km,
                            top_k=5,
                            allowed_crime_types=_FEMALE_SAFETY_CATEGORIES,
                        )
                    except Exception as retry_exc:
                        logger.error("personalised retry failed (%.4f, %.4f): %s", lat, lng, retry_exc)
                        continue
                else:
                    continue
            else:
                logger.error("personalised search failed (%.4f, %.4f): %s", lat, lng, exc)
                continue
        for hit in hits:
            url = hit.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(hit)

    results.sort(key=lambda h: h.get("rrf_score", 0.0), reverse=True)
    return results[:max_total]


def _sample_waypoints(
    waypoints: list[tuple[float, float]],
    interval_m: float,
) -> list[tuple[float, float]]:
    """
    Downsample waypoints to approximately one per interval_m metres.
    Always includes the first and last waypoint.
    """
    import math

    if len(waypoints) <= 1:
        return waypoints

    sampled = [waypoints[0]]
    accumulated = 0.0

    for i in range(1, len(waypoints)):
        prev_lat, prev_lng = waypoints[i - 1]
        curr_lat, curr_lng = waypoints[i]

        # Haversine distance in metres between consecutive waypoints
        dlat = math.radians(curr_lat - prev_lat)
        dlng = math.radians(curr_lng - prev_lng)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(prev_lat))
            * math.cos(math.radians(curr_lat))
            * math.sin(dlng / 2) ** 2
        )
        dist_m = 6_371_000 * 2 * math.asin(math.sqrt(a))

        accumulated += dist_m
        if accumulated >= interval_m:
            sampled.append((curr_lat, curr_lng))
            accumulated = 0.0

    # Always include the destination
    if sampled[-1] != waypoints[-1]:
        sampled.append(waypoints[-1])

    return sampled
