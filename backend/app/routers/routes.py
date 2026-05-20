# backend/app/routers/routes.py

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from prometheus_client import Counter

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.limiter import limiter
from app.schemas.routes import RouteRequest, RouteResponse, RouteOption
from app.services import geocoding, routing
from app.services.risk_model import score_route
from app.services import retrieval_service
from app.schemas.routes import IncidentResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/routes", tags=["routes"])

settings = Settings()

_ROUTES_TOTAL = Counter(
    "routes_recommended_total",
    "Total number of successful route recommendations returned to clients",
)

# Route response cache — keyed on (origin, dest, time_band, profile) rounded to
# 3 decimal places (~111m). TTL=5 min avoids burning ORS quota for repeat queries
# while staying fresh enough that risk bands don't meaningfully drift.
_RESPONSE_CACHE: TTLCache = TTLCache(ttl_seconds=300)


def _time_band(dt: datetime) -> str:
    """Coarsen departure time to one of 4 bands for cache key construction.

    WHY coarsen not exact time: two requests 30 seconds apart for the same
    origin/dest should hit the same cache entry. Exact times would make the
    cache useless for real-world traffic patterns.
    """
    h = dt.hour
    if h >= 22 or h < 5:
        return "night"
    if h >= 18:
        return "evening"
    if h >= 9:
        return "day"
    return "morning"


def _cache_key(
    lat_o: float, lng_o: float,
    lat_d: float, lng_d: float,
    depart_time: datetime,
    profile: str,
) -> str:
    return (
        f"{lat_o:.3f},{lng_o:.3f}"
        f"-{lat_d:.3f},{lng_d:.3f}"
        f"-{_time_band(depart_time)}"
        f"-{profile}"
    )


def _band(score: float, low: float, high: float) -> str:
    if score < low:
        return "Low"
    if score < high:
        return "Medium"
    return "High"


@router.post("/recommend", response_model=RouteResponse)
@limiter.limit("60/minute")
async def recommend(request: Request, req: RouteRequest) -> RouteResponse:
    # WHY request param: slowapi inspects the function signature to extract the
    # client IP. The param must be named "request" and typed starlette.Request.
    try:
        # ── Resolve origin ────────────────────────────────────────────────
        if isinstance(req.origin, str):
            lat_o, lng_o = await geocoding.geocode(req.origin)
            print(f"Geocoded origin {req.origin}")
        else:
            lat_o, lng_o = req.origin.lat, req.origin.lng
            print(f"origin fetched from cache")

        # ── Resolve destination ───────────────────────────────────────────
        if isinstance(req.destination, str):
            lat_d, lng_d = await geocoding.geocode(req.destination)
            print(f"Geocoded destination {req.destination}")
        else:
            lat_d, lng_d = req.destination.lat, req.destination.lng
            print(f"destination fetched from cache")

        # ── Cache check ───────────────────────────────────────────────────
        depart_time = req.depart_time
        if depart_time.tzinfo is None:
            depart_time = depart_time.replace(tzinfo=timezone.utc)

        ck = _cache_key(lat_o, lng_o, lat_d, lng_d, depart_time, "driving-car")
        cached = _RESPONSE_CACHE.get(ck)
        if cached is not None:
            logger.debug("route cache hit", extra={"cache_key": ck})
            return cached

        # ── Fetch alternative routes from ORS ────────────────────────────
        raw_routes = await routing.get_routes(
            origin=(lat_o, lng_o),
            dest=(lat_d, lng_d),
        )
        if not raw_routes:
            raise HTTPException(status_code=502, detail="ORS returned no routes")

        # ── Score each route ─────────────────────────────────────────────
        scored: list[tuple[float, dict]] = []
        for route in raw_routes:
            result = score_route(
                waypoints=route["waypoints"],
                depart_time=depart_time,
                route_eta_sec=route["duration_sec"],
            )
            # WHY log score but not return it: raw float scores for specific
            # neighbourhoods carry defamation risk. Only the band goes to client.
            logger.info(
                "route scored",
                extra={"score": result.total_score, "distance_m": route["distance_m"]},
            )
            scored.append((result.total_score, route))

        # Sort ascending — lowest risk first.
        scored.sort(key=lambda x: x[0])

        options = []
        for score, route in scored:
            # Retrieve nearby historical incidents for this route.
            # WHY after scoring loop: KDE + Qdrant latencies don't compound —
            # we score all routes first, then fetch incidents for each.
            # If Qdrant is unavailable, get_route_incidents returns [] silently.
            raw_incidents = retrieval_service.get_route_incidents(
                waypoints=route["waypoints"],
                radius_km=2.0,
                top_k_per_point=3,
                max_total=5,
            )
            incidents = [
                IncidentResult(
                    crime_macro=i.get("crime_macro", "Unknown"),
                    crime_type=i.get("crime_type"),
                    lat=i.get("lat"),
                    lng=i.get("lng"),
                    crime_date=i.get("crime_date") or None,
                    summary=i.get("summary", ""),
                    url=i.get("url", ""),
                    location_exact=i.get("location_exact"),
                    rrf_score=i.get("rrf_score", 0.0),
                )
                for i in raw_incidents
            ]
            options.append(
                RouteOption(
                    geometry=route["geometry"],
                    duration_sec=route["duration_sec"],
                    distance_m=route["distance_m"],
                    risk_band=_band(score, settings.BAND_LOW_THRESHOLD, settings.BAND_HIGH_THRESHOLD),
                    nearby_incidents=incidents,
                )
            )

        response = RouteResponse(routes=options)
        _RESPONSE_CACHE.set(ck, response)
        _ROUTES_TOTAL.inc()
        return response

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("recommend failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Route service temporarily unavailable",
        ) from exc
