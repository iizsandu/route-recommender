# backend/app/services/routing.py

from __future__ import annotations

import logging
import math

import httpx

from app.config import Settings
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)
settings = Settings()

# 15-min cache — routes between the same two points don't change.
_cache: TTLCache = TTLCache(ttl_seconds=15 * 60)

WAYPOINT_INTERVAL_M = 100  # sample a waypoint every ~100m along the route


def _cache_key(
    origin: tuple[float, float],
    dest: tuple[float, float],
    profile: str,
) -> str:
    # WHY round to 4dp: avoids cache misses from floating-point noise in
    # coordinates that represent the same location (4dp ≈ 11m precision).
    return (
        f"{round(origin[0], 4)},{round(origin[1], 4)}"
        f"|{round(dest[0], 4)},{round(dest[1], 4)}"
        f"|{profile}"
    )


def _haversine_m(a: list[float], b: list[float]) -> float:
    """Great-circle distance in metres between two [lng, lat] points."""
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = math.radians(b[1] - a[1])
    dlng = math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(h))


def _sample_waypoints(coordinates: list[list[float]]) -> list[tuple[float, float]]:
    """
    Walk the GeoJSON coordinate list and emit a (lat, lng) waypoint every
    WAYPOINT_INTERVAL_M metres. GeoJSON is [lng, lat] — we flip on output.
    """
    if not coordinates:
        return []

    waypoints: list[tuple[float, float]] = [(coordinates[0][1], coordinates[0][0])]
    accumulated = 0.0

    for i in range(1, len(coordinates)):
        accumulated += _haversine_m(coordinates[i - 1], coordinates[i])
        if accumulated >= WAYPOINT_INTERVAL_M:
            waypoints.append((coordinates[i][1], coordinates[i][0]))
            accumulated = 0.0

    return waypoints


async def get_routes(
    origin: tuple[float, float],
    dest: tuple[float, float],
    profile: str = "driving-car",
) -> list[dict]:
    """
    Return up to 3 alternative routes from ORS as dicts with keys:
    geometry, duration_sec, distance_m, waypoints.
    Raises httpx.HTTPStatusError if ORS returns an error.
    """
    key = _cache_key(origin, dest, profile)
    cached = _cache.get(key)
    if cached is not None:
        logger.debug("route cache hit for %s", key)
        return cached

    # WHY [lng, lat] in payload: ORS directions API uses GeoJSON coordinate
    # order, which is [longitude, latitude] — opposite of our internal convention.
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.ORS_BASE_URL}/v2/directions/{profile}/geojson",
            headers={"Authorization": settings.ORS_API_KEY},
            json={
                "coordinates": [
                    [origin[1], origin[0]],
                    [dest[1], dest[0]],
                ],
                "alternative_routes": {
                    "share_factor": 0.6,
                    "target_count": 3,
                },
            },
        )
        if resp.status_code == 404:
            # WHY custom message: ORS returns "coordinate 0: 77.24 28.65" which is
            # meaningless to end users. Translate to actionable guidance.
            raise ValueError(
                "One or more locations could not be matched to a drivable road. "
                "Try entering a nearby street name or landmark instead."
            )
        resp.raise_for_status()

    routes = []
    for feature in resp.json().get("features", []):
        summary = feature["properties"]["summary"]
        routes.append({
            "geometry":     feature["geometry"],
            "duration_sec": summary["duration"],
            "distance_m":   summary["distance"],
            "waypoints":    _sample_waypoints(feature["geometry"]["coordinates"]),
        })

    _cache.set(key, routes)
    logger.info("fetched %d routes from ORS (%s → %s)", len(routes), origin, dest)
    return routes
