# backend/app/services/geocoding.py
#
# Geocoding chain: Mappls (MapMyIndia) → Nominatim fallback
#
# WHY Mappls first: India-native dataset with 50M+ places. Handles Delhi
# locality names, colony names, and informal addresses far better than
# OSM-based services (Pelias/Nominatim).
#
# WHY Nominatim fallback: free, no quota, covers well-known landmarks that
# Mappls might miss due to the 250 req/day free-tier cap.
#
# ORS geocoding removed — it used the same OSM data as Nominatim but with
# an extra network hop through ORS servers.

from __future__ import annotations

import httpx

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.logger import get_logger

logger = get_logger(__name__)
settings = Settings()

_cache: TTLCache = TTLCache(ttl_seconds=24 * 3600)


async def _mappls(address: str) -> tuple[float, float] | None:
    """
    Call Mappls geocoding API.
    Returns (lat, lng) on success, None if address not found.
    Raises httpx exceptions on network/HTTP errors (caller catches and falls back).
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://apis.mappls.com/advancedmaps/v2/geocode",
            params={
                "access_token": settings.MAPPLS_API_KEY,
                "address": address,
                "itemCount": 1,
            },
        )
        resp.raise_for_status()

    # WHY copResults: Mappls wraps results under this key. It can be a dict
    # (single result) or a list depending on the query type.
    cop = resp.json().get("copResults")
    if not cop:
        return None
    if isinstance(cop, list):
        if not cop:
            return None
        cop = cop[0]

    lat, lng = cop.get("lat"), cop.get("lng")
    if lat is None or lng is None:
        return None
    return (float(lat), float(lng))


async def _nominatim(address: str) -> tuple[float, float] | None:
    """
    Nominatim (OpenStreetMap) fallback geocoder.
    Returns (lat, lng) on success, None if not found.

    WHY User-Agent header: Nominatim usage policy requires identifying the app.
    Requests without a User-Agent are rejected.
    WHY bounded=1 + viewbox: restricts results to Delhi-NCR so a generic name
    like "Saket" doesn't resolve to a city in another state.
    """
    async with httpx.AsyncClient(
        timeout=10.0,
        headers={"User-Agent": "RouteRecommenderDelhi/1.0 (sandip111shaw@gmail.com)"},
    ) as client:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": address,
                "format": "json",
                "limit": 1,
                "countrycodes": "in",
                # WHY this order: Nominatim viewbox is lng_min,lat_max,lng_max,lat_min
                "viewbox": "76.5,29.5,78.0,28.0",
                "bounded": 1,
            },
        )
        resp.raise_for_status()

    results = resp.json()
    if not results:
        return None
    # WHY "lon" not "lng": Nominatim uses the non-standard key "lon"
    return (float(results[0]["lat"]), float(results[0]["lon"]))


async def geocode(address: str) -> tuple[float, float]:
    """
    Geocode an address string to (lat, lng).

    Chain: Mappls → Nominatim fallback → ValueError if both fail.
    Results cached 24 h — addresses don't move.
    """
    key = address.strip().lower()
    cached = _cache.get(key)
    if cached is not None:
        return cached

    # ── Mappls (primary) ──────────────────────────────────────────────────
    if settings.MAPPLS_API_KEY:
        try:
            result = await _mappls(address)
            if result:
                logger.info("mappls geocoded", address=address, lat=result[0], lng=result[1])
                _cache.set(key, result)
                return result
            logger.info("mappls no result — trying Nominatim", address=address)
        except Exception as exc:
            logger.warning("mappls geocode error — trying Nominatim", address=address, error=str(exc))

    # ── Nominatim fallback ────────────────────────────────────────────────
    try:
        result = await _nominatim(address)
        if result:
            logger.info("nominatim geocoded", address=address, lat=result[0], lng=result[1])
            _cache.set(key, result)
            return result
    except Exception as exc:
        logger.warning("nominatim geocode error", address=address, error=str(exc))

    raise ValueError(f"could not geocode address: {address!r}")
