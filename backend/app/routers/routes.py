# backend/app/routers/routes.py

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from prometheus_client import Counter

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.limiter import limiter
from app.schemas.routes import RouteRequest, RouteResponse, RouteOption, PersonalisedRequest
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
# 3 decimal places (~111m). TTL=5 min keeps responses fresh while avoiding
# redundant GraphHopper + KDE calls for repeated queries.
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
) -> str:
    return (
        f"{lat_o:.3f},{lng_o:.3f}"
        f"-{lat_d:.3f},{lng_d:.3f}"
        f"-{_time_band(depart_time)}"
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
            logger.debug("geocoded origin: %s → (%.4f, %.4f)", req.origin, lat_o, lng_o)
        else:
            lat_o, lng_o = req.origin.lat, req.origin.lng
            logger.debug("origin from coordinates: (%.4f, %.4f)", lat_o, lng_o)

        # ── Resolve destination ───────────────────────────────────────────
        if isinstance(req.destination, str):
            lat_d, lng_d = await geocoding.geocode(req.destination)
            logger.debug("geocoded destination: %s → (%.4f, %.4f)", req.destination, lat_d, lng_d)
        else:
            lat_d, lng_d = req.destination.lat, req.destination.lng
            logger.debug("destination from coordinates: (%.4f, %.4f)", lat_d, lng_d)

        # ── Cache check ───────────────────────────────────────────────────
        depart_time = req.depart_time
        if depart_time.tzinfo is None:
            depart_time = depart_time.replace(tzinfo=timezone.utc)

        ck = _cache_key(lat_o, lng_o, lat_d, lng_d, depart_time)
        cached = _RESPONSE_CACHE.get(ck)
        if cached is not None:
            logger.debug("route cache hit", extra={"cache_key": ck})
            return cached

        # ── Fetch both profiles in parallel ──────────────────────────────
        # WHY parallel: two independent GH requests; sequential would double latency.
        # WHY two profiles not alternative_route: for tight corridors GH alternative_route
        # returns only 1 path because no true geometric alternatives exist. Two profiles
        # always yield two meaningfully different trade-offs.
        _PROFILES = ["fastest", "safest"]
        gh_results = await asyncio.gather(
            *[
                routing.get_routes(origin=(lat_o, lng_o), dest=(lat_d, lng_d), profile=p)
                for p in _PROFILES
            ],
            return_exceptions=True,
        )

        # Collect one route per profile; skip if GH failed for that profile.
        raw_routes: list[dict] = []
        for profile, result in zip(_PROFILES, gh_results):
            if isinstance(result, Exception):
                logger.warning("GH profile %s failed: %s", profile, result)
                continue
            if result:
                best = dict(result[0])   # copy so we can annotate without mutating cache
                best["route_type"] = profile
                raw_routes.append(best)

        if not raw_routes:
            raise HTTPException(status_code=502, detail="GraphHopper returned no routes")

        # ── Score each route ─────────────────────────────────────────────
        scored: list[tuple[float, dict]] = []
        for route in raw_routes:
            result = score_route(
                waypoints=route["waypoints"],
                depart_time=depart_time,
                route_eta_sec=route["duration_sec"],
                kde_weight=settings.KDE_ENSEMBLE_WEIGHT,
                lgb_weight=settings.LGB_ENSEMBLE_WEIGHT,
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

        # WHY re-label by rank: GH profile names ("fastest"/"safest") describe the
        # routing strategy, not the KDE outcome. After independent scoring, the
        # "fastest" GH path can outscore "safest" on KDE — producing contradictory
        # "Safest Route — Medium Risk" vs "Fastest Route — Low Risk" labels.
        # Reassigning by rank guarantees the displayed label always matches the band.
        _RANK_LABELS = ["safest", "fastest"]
        for rank, (_, route) in enumerate(scored):
            route["route_type"] = _RANK_LABELS[rank] if rank < len(_RANK_LABELS) else "fastest"

        options = []
        for score, route in scored:
            # Retrieve nearby historical incidents for this route.
            # WHY after scoring loop: KDE + Qdrant latencies don't compound —
            # we score all routes first, then fetch incidents for each.
            # If Qdrant is unavailable, get_route_incidents returns [] silently.
            raw_incidents = await asyncio.to_thread(
                retrieval_service.get_route_incidents,
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
                    victim=i.get("victim"),
                    weapon_used=i.get("weapon_used"),
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
                    route_type=route.get("route_type", "balanced"),
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


@router.post("/incidents/personalised", response_model=list[IncidentResult])
@limiter.limit("60/minute")
async def personalised_incidents(
    request: Request,
    req: PersonalisedRequest,
) -> list[IncidentResult]:
    raw = await asyncio.to_thread(
        retrieval_service.get_personalised_incidents,
        situation_text=req.situation,
        waypoints=req.waypoints,
        radius_km=req.radius_km,
        max_total=req.max_total,
    )
    return [
        IncidentResult(
            crime_macro=i.get("crime_macro", "Unknown"),
            crime_type=i.get("crime_type"),
            lat=i.get("lat"),
            lng=i.get("lng"),
            crime_date=i.get("crime_date") or None,
            summary=i.get("summary", ""),
            url=i.get("url", ""),
            location_exact=i.get("location_exact"),
            victim=i.get("victim"),
            weapon_used=i.get("weapon_used"),
            rrf_score=i.get("rrf_score", 0.0),
        )
        for i in raw
    ]
