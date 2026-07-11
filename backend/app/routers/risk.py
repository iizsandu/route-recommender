# backend/app/routers/risk.py
"""
GET /risk/heatmap — serve the pre-computed static risk heatmap GeoJSON.

The file is generated weekly by ml/generate_heatmap.py and stored at
HEATMAP_PATH (default: ml/artifacts/heatmap.geojson). It is loaded once
into memory on the first request and cached for the process lifetime.

WHY static file not live computation: scoring ~5,700 grid points takes
~200ms — too slow to block a map load. The weekly regeneration cadence
matches the model retrain cadence, so the heatmap is always current.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from app.config import Settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/risk", tags=["risk"])

settings = Settings()

# Module-level cache — loaded once on first request, held for process lifetime.
_HEATMAP_CACHE: dict | None = None


def _resolve_heatmap_path() -> Path:
    p = Path(settings.HEATMAP_PATH)
    if not p.is_absolute():
        repo_root = Path(__file__).resolve().parents[3]  # backend/app/routers/risk.py → repo root
        p = repo_root / p
    return p


def _load_heatmap() -> dict:
    global _HEATMAP_CACHE
    if _HEATMAP_CACHE is not None:
        return _HEATMAP_CACHE

    path = _resolve_heatmap_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Heatmap not found at {path}. "
            "Run `python -m ml.generate_heatmap` to generate it."
        )

    with open(path, encoding="utf-8") as f:
        _HEATMAP_CACHE = json.load(f)

    n = len(_HEATMAP_CACHE.get("features", []))
    logger.info("heatmap loaded into memory: %d features from %s", n, path)
    return _HEATMAP_CACHE


@router.get("/heatmap")
async def heatmap() -> JSONResponse:
    """Return the pre-computed risk heatmap as a GeoJSON FeatureCollection.

    Each feature is a Point with a `risk_band` property ("Low"/"Medium"/"High").
    Regenerated weekly alongside the model retrain. Returns 503 if the heatmap
    file has not yet been generated.
    """
    try:
        data = _load_heatmap()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("failed to load heatmap: %s", exc)
        raise HTTPException(status_code=500, detail="Heatmap unavailable") from exc

    # WHY JSONResponse not plain dict return: avoids Pydantic serialisation
    # overhead on a 400KB payload that is already valid JSON in memory.
    return JSONResponse(content=data)


VALID_CATEGORIES = {
    "all", "sexual_violence", "robbery", "assault",
    "kidnapping", "murder", "theft_burglary",
    "drug_trafficking", "terrorism_riot",
}


def _resolve_image_path(category: str = "all") -> Path:
    p = Path(settings.HEATMAP_IMAGE_PATH)
    if not p.is_absolute():
        repo_root = Path(__file__).resolve().parents[3]
        p = repo_root / p
    # Category-specific file takes priority; fall back to legacy heatmap.png
    category_path = p.parent / f"heatmap_{category}.png"
    if category_path.exists():
        return category_path
    return p


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SNAPSHOT_DIR = _REPO_ROOT / "ml" / "data" / "snapshots"

# Cached once for the process lifetime — same pattern as heatmap.
_CRIMES_GEOJSON_CACHE: dict | None = None


def _clean_str(val) -> str | None:
    if val is None:
        return None
    import math
    try:
        if isinstance(val, float) and math.isnan(val):
            return None
    except TypeError:
        pass
    s = str(val)
    return s if s not in ("", "nan", "None", "NaT") else None


def _build_crimes_geojson() -> dict:
    global _CRIMES_GEOJSON_CACHE
    if _CRIMES_GEOJSON_CACHE is not None:
        return _CRIMES_GEOJSON_CACHE

    features = []

    # ── Primary: Parquet snapshot (fastest) ──────────────────────────────
    snapshots = sorted(_SNAPSHOT_DIR.glob("crimes_*.parquet"))
    if snapshots:
        import pandas as pd
        df = pd.read_parquet(snapshots[-1])
        df = df.dropna(subset=["lat", "lng"])
        df = df[(df["lat"] >= 28.0) & (df["lat"] <= 29.5) &
                (df["lng"] >= 76.5) & (df["lng"] <= 78.0)]

        # WHY: crimes with no known location were assigned the Delhi centre
        # coordinate (28.6139, 77.2090) as a placeholder during extraction.
        # 797 records share this exact point — rendering them produces a
        # misleading cluster that dissolves into invisible stacked dots.
        # Threshold of 20 is well above any realistic crime density at one
        # exact lat/lng pair; genuine hotspots (police stations, courts)
        # top out around 10–15 records at a single precise coordinate.
        coord_counts = df.groupby(["lat", "lng"]).size()
        valid_coords = coord_counts[coord_counts <= 20].index
        before = len(df)
        df = df[df.set_index(["lat", "lng"]).index.isin(valid_coords)]
        dropped = before - len(df)
        if dropped:
            logger.warning(
                "crimes GeoJSON: dropped %d records with placeholder coordinates "
                "(>20 crimes at identical lat/lng)",
                dropped,
            )

        for row in df.itertuples(index=False):
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [float(row.lng), float(row.lat)]},
                "properties": {
                    "crime_macro":    _clean_str(getattr(row, "crime_macro", None)),
                    "crime_type":     _clean_str(getattr(row, "crime_type", None)),
                    "crime_date":     _clean_str(getattr(row, "crime_date", None)),
                    "url":            _clean_str(getattr(row, "url", None)) or "",
                    "location_exact": _clean_str(getattr(row, "location_exact", None)),
                    "victim":         _clean_str(getattr(row, "victim", None)),
                    "weapon_used":    _clean_str(getattr(row, "weapon_used", None)),
                },
            })
        logger.info("crimes GeoJSON built from Parquet: %d features", len(features))

    # ── Fallback: scroll Qdrant (used when no Parquet snapshot exists) ───
    else:
        logger.info("No Parquet snapshot — scrolling Qdrant for crimes GeoJSON")
        from app.services import retrieval_service  # noqa: PLC0415
        client = retrieval_service._client
        if client is None:
            logger.warning("Qdrant not available — crimes GeoJSON will be empty")
        else:
            offset = None
            while True:
                result = client.scroll(
                    collection_name="delhi_crimes",
                    scroll_filter=None,
                    limit=500,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                points, next_offset = result
                for pt in points:
                    p = pt.payload or {}
                    lat = p.get("lat")
                    lng = p.get("lng")
                    if lat is None or lng is None:
                        continue
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "Point",
                                     "coordinates": [float(lng), float(lat)]},
                        "properties": {
                            "crime_macro":    _clean_str(p.get("crime_macro")),
                            "crime_type":     _clean_str(p.get("crime_type")),
                            "crime_date":     _clean_str(p.get("crime_date")),
                            "url":            _clean_str(p.get("url")) or "",
                            "location_exact": _clean_str(p.get("location_exact")),
                            "victim":         _clean_str(p.get("victim")),
                            "weapon_used":    _clean_str(p.get("weapon_used")),
                            "summary":        _clean_str(p.get("summary")),
                        },
                    })
                if next_offset is None:
                    break
                offset = next_offset
            # Strip placeholder coordinates — same logic as Parquet path above.
            from collections import Counter
            coord_counts = Counter(
                (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
                for f in features
            )
            before = len(features)
            features = [
                f for f in features
                if coord_counts[
                    (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
                ] <= 20
            ]
            dropped = before - len(features)
            if dropped:
                logger.warning(
                    "crimes GeoJSON: dropped %d records with placeholder coordinates "
                    "(>20 crimes at identical lat/lng)",
                    dropped,
                )
            logger.info("crimes GeoJSON built from Qdrant: %d features", len(features))

    _CRIMES_GEOJSON_CACHE = {"type": "FeatureCollection", "features": features}
    return _CRIMES_GEOJSON_CACHE


@router.get("/crimes-geojson")
async def crimes_geojson() -> JSONResponse:
    """Return all crime records with valid Delhi-NCR coordinates as GeoJSON.

    Loaded once from the latest Parquet snapshot and cached for the process
    lifetime. Used by the frontend 'All Crimes' toggle to render a clustered
    dot layer on the map.
    """
    try:
        data = _build_crimes_geojson()
    except Exception as exc:
        logger.error("failed to build crimes GeoJSON: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Crime data unavailable") from exc
    return JSONResponse(content=data)


@router.get("/heatmap-image")
async def heatmap_image(category: str = "all") -> FileResponse:
    """Serve a pre-computed KDE risk surface PNG for the given crime category.

    ?category= accepts: all (default), sexual_violence, robbery, assault,
    kidnapping, murder, theft_burglary. Falls back to heatmap.png if
    category-specific file not yet generated.
    """
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category: {category!r}")

    path = _resolve_image_path(category)
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="Heatmap image not found. Run `python -m ml.generate_heatmap`.",
        )
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )
