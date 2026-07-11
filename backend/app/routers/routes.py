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
from app.services import geocoding, routing, risk_model
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


def _band(total_score: float, duration_sec: float, low: float, high: float) -> str:
    # WHY normalize: total_score = Σ(density × dwell_sec), so a 10-min route through
    # equally-dense streets scores 2× a 5-min route. Normalizing to a 5-min reference
    # makes banding reflect the *density* of crime on the route (street danger level),
    # not how long you happen to spend on it.
    normalized = total_score / max(duration_sec, 1.0) * 300.0
    if normalized < low:
        return "Low"
    if normalized < high:
        return "Medium"
    return "High"


def _avg_density(total_score: float, duration_sec: float) -> float:
    """Crime density per second — used to select the safest route independently of length."""
    return total_score / max(duration_sec, 1.0)


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

        # ── Collect routes from both GH profiles ─────────────────────────
        # GH already computes up to 10 geometrically distinct alternatives per
        # profile via algorithm=alternative_route. We pool both profiles so the
        # KDE scorer sees the widest possible set of corridors before picking the
        # two best routes to return. Previously only fastest_gh[0] was used —
        # routes[1..9] were thrown away before their KDE scores were checked.
        all_raw: list[dict] = []

        fastest_gh = await routing.get_routes(
            origin=(lat_o, lng_o), dest=(lat_d, lng_d), profile="fastest"
        )
        all_raw.extend(fastest_gh)

        try:
            safest_gh = await routing.get_routes(
                origin=(lat_o, lng_o), dest=(lat_d, lng_d), profile="safest"
            )
            all_raw.extend(safest_gh)
        except Exception as exc:
            logger.warning("GH safest profile failed (non-fatal): %s", exc)

        # ── Score all unique routes with KDE ──────────────────────────────
        # Deduplicate across profiles by geometry fingerprint (10-point hash).
        # _route_fingerprint is a private helper in routing.py — stable enough
        # to reuse here; if it moves, update this import.
        from app.services.routing import _route_fingerprint  # noqa: PLC0415

        seen_fps: set = set()
        scored: list[tuple[float, dict]] = []
        for route in all_raw:
            r = dict(route)
            fp = _route_fingerprint(r["geometry"]["coordinates"])
            if fp in seen_fps:
                continue
            seen_fps.add(fp)

            result = score_route(
                waypoints=r["waypoints"],
                depart_time=depart_time,
                route_eta_sec=r["duration_sec"],
                kde_weight=settings.KDE_ENSEMBLE_WEIGHT,
                lgb_weight=settings.LGB_ENSEMBLE_WEIGHT,
            )
            scored.append((result.total_score, r))
            logger.info(
                "candidate route: kde=%.1f  dur=%.0fs  dist=%.0fm",
                result.total_score, r["duration_sec"], r["distance_m"],
            )

        if not scored:
            raise HTTPException(status_code=502, detail="GraphHopper returned no routes")

        # ── Pick Fastest (min duration) and Safest (min avg density) ─────
        # WHY avg density not total score: total = density × duration, so a long
        # detour through safe streets scores higher than a short route through
        # the same streets. Avg density = total/duration makes selection
        # route-length-neutral — we find the corridor with fewest crimes per
        # second of travel, regardless of how long the journey takes.
        fastest_score, fastest_route = min(scored, key=lambda x: x[1]["duration_sec"])
        fastest_route["route_type"] = "fastest"

        safest_score, safest_route = min(
            scored, key=lambda x: _avg_density(x[0], x[1]["duration_sec"])
        )
        safest_route["route_type"] = "safest"

        # Always try to return 2 routes. If fastest and safest are the same route
        # (same dict object), find the next-best safest from the remaining pool.
        if safest_route is fastest_route:
            others = [(s, r) for s, r in scored if r is not fastest_route]
            if others:
                safest_score, safest_route = min(
                    others, key=lambda x: _avg_density(x[0], x[1]["duration_sec"])
                )
                safest_route["route_type"] = "safest"
                final: list[tuple[float, dict]] = [
                    (fastest_score, fastest_route),
                    (safest_score, safest_route),
                ]
            else:
                # Only one unique route exists for this trip
                final: list[tuple[float, dict]] = [(fastest_score, fastest_route)]
        else:
            final: list[tuple[float, dict]] = [
                (fastest_score, fastest_route),
                (safest_score, safest_route),
            ]

        options = []
        for score, route in final:
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
                    risk_band=_band(score, route["duration_sec"], settings.BAND_LOW_THRESHOLD, settings.BAND_HIGH_THRESHOLD),
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
