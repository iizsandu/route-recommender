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

    with open(path, "r", encoding="utf-8") as f:
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
