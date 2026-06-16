# backend/app/services/routing.py
"""
Routing service — wraps GraphHopper's POST /route endpoint.

External interface is unchanged from the ORS version:
  get_routes(origin, dest, profile) -> list[dict]
  Each dict has keys: geometry, duration_sec, distance_m, waypoints.

The downstream scoring (KDE), banding, and Qdrant retrieval are untouched.
"""
from __future__ import annotations

import logging
import math

import httpx

from app.config import Settings
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)
settings = Settings()

# 15-min cache — routes between the same two points don't change quickly.
_cache: TTLCache = TTLCache(ttl_seconds=15 * 60)

# Sample a waypoint every ~100m for KDE scoring.
WAYPOINT_INTERVAL_M = 100

# Delhi NCT bounding box. Requests outside this box will fail at the GH level
# because the Delhi-only PBF doesn't contain those roads; we catch it early.
_LAT_MIN, _LAT_MAX = 28.40, 28.88
_LNG_MIN, _LNG_MAX = 76.84, 77.35


def _within_delhi_nct(lat: float, lng: float) -> bool:
    return _LAT_MIN <= lat <= _LAT_MAX and _LNG_MIN <= lng <= _LNG_MAX


def _cache_key(
    origin: tuple[float, float],
    dest: tuple[float, float],
    profile: str,
) -> str:
    # Round to 4dp (≈11m) to share cache entries for nearby repeated queries.
    return (
        f"{round(origin[0], 4)},{round(origin[1], 4)}"
        f"|{round(dest[0], 4)},{round(dest[1], 4)}"
        f"|{profile}"
    )


def _haversine_m(a: list[float], b: list[float]) -> float:
    """Great-circle distance in metres between two [lng, lat] GeoJSON points."""
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = math.radians(b[1] - a[1])
    dlng = math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(h))


def _sample_waypoints(coordinates: list[list[float]]) -> list[tuple[float, float]]:
    """
    Walk a GeoJSON coordinate list and emit one (lat, lng) waypoint every
    WAYPOINT_INTERVAL_M metres. GeoJSON is [lng, lat] — we flip on output.
    Identical to the ORS version; works for any GeoJSON LineString source.
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


def _route_fingerprint(coordinates: list[list[float]]) -> tuple:
    """
    Sample up to 10 evenly-spaced coordinates and round to 3dp (~111m).
    Two routes with identical fingerprints are duplicates.
    WHY 3dp not 4dp: at 3dp adjacent roads in Delhi are still distinct (~111m);
    at 4dp two routes that share a brief common segment could fingerprint as different.
    """
    n = len(coordinates)
    if n == 0:
        return ()
    step = max(1, (n - 1) // 9)
    indices = list(range(0, n, step))[:10]
    return tuple(
        (round(coordinates[i][0], 3), round(coordinates[i][1], 3))
        for i in indices
    )


def _deduplicate_routes(routes: list[dict]) -> list[dict]:
    """
    Drop routes with identical geometry fingerprints.
    GraphHopper occasionally returns the same path twice when only one corridor
    exists between A and B and the alternative-route algorithm can't find true
    alternatives.
    """
    seen: set = set()
    unique = []
    for route in routes:
        fp = _route_fingerprint(route["geometry"]["coordinates"])
        if fp not in seen:
            seen.add(fp)
            unique.append(route)
        else:
            logger.debug("dropped duplicate route (same fingerprint)")
    return unique


async def get_routes(
    origin: tuple[float, float],
    dest: tuple[float, float],
    profile: str | None = None,
) -> list[dict]:
    """
    Return up to 3 deduplicated routes from GraphHopper as dicts with keys:
      geometry, duration_sec, distance_m, waypoints.
    Raises HTTPException(503) if GraphHopper is unreachable.
    Raises ValueError for out-of-bounds coordinates.
    """
    from fastapi import HTTPException  # local import to avoid circular import

    # ── Validate profile ──────────────────────────────────────────────────────
    if profile is None:
        profile = settings.SAFETY_PROFILE
    valid_profiles = set(settings.GRAPHHOPPER_PROFILES.split(","))
    if profile not in valid_profiles:
        raise ValueError(
            f"Unknown profile '{profile}'. Valid values: {sorted(valid_profiles)}"
        )

    # ── Validate coordinates are within the Delhi NCT graph ──────────────────
    for label, lat, lng in [
        ("origin", origin[0], origin[1]),
        ("destination", dest[0], dest[1]),
    ]:
        if not _within_delhi_nct(lat, lng):
            raise ValueError(
                f"{label.capitalize()} ({lat:.4f}, {lng:.4f}) is outside the Delhi NCT "
                f"routing area (lat {_LAT_MIN}–{_LAT_MAX}, lng {_LNG_MIN}–{_LNG_MAX}). "
                "Gurgaon and Noida are not available in Phase 8."
            )

    # ── Cache check ───────────────────────────────────────────────────────────
    key = _cache_key(origin, dest, profile)
    cached = _cache.get(key)
    if cached is not None:
        logger.debug("route cache hit for %s", key)
        return cached

    # ── GraphHopper request ───────────────────────────────────────────────────
    # GH uses [lng, lat] order (GeoJSON convention) — same as ORS.
    payload = {
        "points": [
            [origin[1], origin[0]],
            [dest[1], dest[0]],
        ],
        "profile": profile,
        "algorithm": "alternative_route",
        "alternative_route.max_paths": 10,
        "alternative_route.max_weight_factor": 2.0,
        "alternative_route.max_share_factor": 0.6,
        "points_encoded": False,   # return full GeoJSON, not polyline6
        "instructions": False,     # no turn-by-turn; saves ~40% payload size
        "locale": "en",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{settings.GRAPHHOPPER_URL}/route",
                json=payload,
            )
            resp.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=(
                "Routing service (GraphHopper) is unavailable. "
                "Ensure the GraphHopper container is running: docker compose up graphhopper"
            ),
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:400]
        logger.error(
            "GraphHopper %d for profile=%s origin=%s dest=%s body=%s",
            exc.response.status_code, profile, origin, dest, body,
        )
        # Extract GH's human-readable message field when present.
        try:
            import json as _json
            gh_msg = _json.loads(body).get("message", body)
        except Exception:
            gh_msg = body

        if exc.response.status_code == 400 and "Connection" in gh_msg:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No road connection found between these locations. "
                    "This usually means one address is outside the Delhi NCT road network "
                    "(e.g. inside a campus, park, or building with no mapped road access). "
                    "Try a nearby landmark or main road instead."
                ),
            )
        raise HTTPException(
            status_code=502,
            detail=f"Routing service error ({exc.response.status_code}): {gh_msg}",
        )

    # ── Parse GH response ────────────────────────────────────────────────────
    # GH response: {"paths": [{"points": GeoJSON, "time": ms, "distance": m}, ...]}
    # ORS response was: {"features": [{"geometry": GeoJSON, "properties": {"summary": ...}}]}
    routes = []
    for path in resp.json().get("paths", []):
        geojson = path["points"]   # {"type": "LineString", "coordinates": [...]}
        routes.append({
            "geometry":     geojson,
            "duration_sec": path["time"] / 1000.0,   # GH gives ms; convert to seconds
            "distance_m":   path["distance"],         # already metres, same as ORS
            "waypoints":    _sample_waypoints(geojson["coordinates"]),
        })

    routes = _deduplicate_routes(routes)
    if not routes:
        raise HTTPException(status_code=502, detail="GraphHopper returned no routes")

    _cache.set(key, routes)
    logger.info(
        "fetched %d routes from GraphHopper profile=%s (%s → %s)",
        len(routes), profile, origin, dest,
    )
    return routes
