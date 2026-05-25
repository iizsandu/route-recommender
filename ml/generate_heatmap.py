# ml/generate_heatmap.py
"""
Pre-compute static risk heatmap PNGs — one per crime category plus a
female-safety-weighted "all" blend.

Each PNG is a georeferenced RGBA image covering the Delhi-NCR bounding box.
The backend serves them via GET /risk/heatmap-image?category=<slug>.
The frontend swaps the MapLibre raster source URL when the user picks a category.

Output files (in artifacts_dir):
    heatmap_all.png
    heatmap_sexual_violence.png
    heatmap_robbery.png
    heatmap_assault.png
    heatmap_kidnapping.png
    heatmap_murder.png
    heatmap_theft_burglary.png
    heatmap.png          ← legacy alias for heatmap_all.png (backward compat)
    heatmap.geojson      ← unchanged (used by /risk/heatmap endpoint)

Usage:
    python -m ml.generate_heatmap
    python -m ml.generate_heatmap --artifacts ml/artifacts --grid-size 400
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

LAT_MIN, LAT_MAX = 28.0, 29.5
LNG_MIN, LNG_MAX = 76.5, 78.0
GRID_STEP  = 0.02   # for GeoJSON (~5,700 points)
DENSE_SIZE = 400    # for PNG (400×400 pixels, ~14 km/pixel)
HOUR       = 12     # daytime neutral baseline for "all" blend

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"

BAND_LOW  = 0.0713
BAND_HIGH = 0.9142

_TIME_BANDS = (
    (22, 24, 2.5), (0, 5, 2.5),
    (18, 22, 1.5),
    (5,  9,  1.0),
    (9,  18, 0.7),
)

# Map KDE artifact display name → URL slug (must match CATEGORY_CMAPS keys)
CATEGORY_SLUGS: dict[str, str] = {
    "Sexual Violence":    "sexual_violence",
    "Robbery":            "robbery",
    "Assault":            "assault",
    "Kidnapping":         "kidnapping",
    "Murder":             "murder",
    "Theft / Burglary":   "theft_burglary",
    "Drug / Trafficking": "drug_trafficking",
    "Terrorism / Riot":   "terrorism_riot",
}

# Per-category RGBA colormap stops.
# WHY distinct color per category: instant visual cue when switching —
# user knows they changed something without reading the label.
CATEGORY_CMAPS: dict[str, list] = {
    "all": [
        (0.00, (1.00, 1.00, 1.00, 0.00)),
        (0.12, (1.00, 0.95, 0.50, 0.18)),
        (0.30, (1.00, 0.70, 0.10, 0.55)),
        (0.55, (1.00, 0.25, 0.00, 0.78)),
        (0.75, (0.85, 0.00, 0.00, 0.88)),
        (1.00, (0.45, 0.00, 0.00, 0.95)),
    ],
    "sexual_violence": [
        (0.00, (1.00, 1.00, 1.00, 0.00)),
        (0.15, (1.00, 0.85, 0.90, 0.20)),
        (0.40, (0.95, 0.30, 0.50, 0.65)),
        (0.70, (0.80, 0.00, 0.20, 0.85)),
        (1.00, (0.45, 0.00, 0.10, 0.95)),
    ],
    "robbery": [
        (0.00, (1.00, 1.00, 1.00, 0.00)),
        (0.15, (1.00, 0.98, 0.70, 0.20)),
        (0.40, (1.00, 0.80, 0.00, 0.60)),
        (0.70, (0.90, 0.50, 0.00, 0.82)),
        (1.00, (0.60, 0.25, 0.00, 0.95)),
    ],
    "assault": [
        (0.00, (1.00, 1.00, 1.00, 0.00)),
        (0.15, (1.00, 0.90, 0.75, 0.20)),
        (0.40, (0.95, 0.55, 0.10, 0.62)),
        (0.70, (0.75, 0.25, 0.00, 0.83)),
        (1.00, (0.40, 0.10, 0.00, 0.95)),
    ],
    "kidnapping": [
        (0.00, (1.00, 1.00, 1.00, 0.00)),
        (0.15, (0.90, 0.80, 1.00, 0.20)),
        (0.40, (0.65, 0.30, 0.90, 0.62)),
        (0.70, (0.45, 0.05, 0.75, 0.83)),
        (1.00, (0.20, 0.00, 0.45, 0.95)),
    ],
    "murder": [
        (0.00, (1.00, 1.00, 1.00, 0.00)),
        (0.15, (0.85, 0.85, 0.85, 0.20)),
        (0.40, (0.50, 0.50, 0.50, 0.62)),
        (0.70, (0.20, 0.20, 0.20, 0.83)),
        (1.00, (0.00, 0.00, 0.00, 0.95)),
    ],
    "theft_burglary": [
        (0.00, (1.00, 1.00, 1.00, 0.00)),
        (0.15, (0.75, 0.95, 0.90, 0.20)),
        (0.40, (0.10, 0.75, 0.65, 0.60)),
        (0.70, (0.00, 0.50, 0.45, 0.82)),
        (1.00, (0.00, 0.25, 0.22, 0.95)),
    ],
}


def _time_modifier(hour: int) -> float:
    for start, end, mult in _TIME_BANDS:
        if start <= hour < end:
            return mult
    return 1.0


def _load_models(artifacts_dir: Path) -> tuple[dict, dict]:
    pkl_files = sorted(artifacts_dir.glob("kde_*.pkl"))
    if not pkl_files:
        raise FileNotFoundError(f"No kde_*.pkl in {artifacts_dir} — run train_kde.py first.")

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    models: dict = {}
    weights: dict = {}
    for pkl_path in pkl_files:
        with open(pkl_path, "rb") as f:
            artifact = pickle.load(f)
        cat = artifact["category"]
        models[cat]  = artifact["kde"]
        weights[cat] = artifact["weight"]

    logger.info("loaded %d KDE models from %s", len(models), artifacts_dir)
    return models, weights


def _score_dense_grid(
    models: dict,
    weights: dict,
    grid_size: int = DENSE_SIZE,
    single_cat: str | None = None,
) -> np.ndarray:
    """
    Score KDE on a (grid_size × grid_size) grid.

    single_cat=None  → weighted blend of all categories (the "all" heatmap).
    single_cat="X"   → score only category X, unweighted.

    WHY unweighted for single-category: the weight is a relative importance
    across categories. When viewing just one category, we want to see its
    own geographic distribution — not a number that is 3× someone else's.
    """
    lats = np.linspace(LAT_MIN, LAT_MAX, grid_size)
    lngs = np.linspace(LNG_MIN, LNG_MAX, grid_size)
    lat_g, lng_g = np.meshgrid(lats, lngs, indexing="ij")
    pts = np.vstack([lat_g.ravel(), lng_g.ravel()])

    scores = np.zeros(pts.shape[1])

    if single_cat is not None:
        if single_cat in models:
            scores = models[single_cat](pts).astype(float)
        else:
            logger.warning("no KDE model for category %r", single_cat)
    else:
        for cat, kde in models.items():
            w = weights.get(cat, 0.0)
            if w > 0:
                scores += kde(pts) * w
        scores *= _time_modifier(HOUR)

    logger.info(
        "dense grid %dx%d cat=%s  min=%.4f  max=%.4f",
        grid_size, grid_size,
        single_cat or "all",
        scores.min(), scores.max(),
    )
    return scores.reshape(grid_size, grid_size)


def _scores_to_image(
    scores_2d: np.ndarray,
    out_path: Path,
    cmap_stops: list,
) -> None:
    """
    Render score array as a transparent RGBA PNG.

    WHY log-percentile normalisation: raw KDE scores are right-skewed
    (a few peak cells 10–50× the median). Linear normalisation compresses
    most of the map to near-zero, producing a faint blob everywhere except
    one bright spike. Log + percentile clip spreads the dynamic range so
    neighbourhood-level differences are visible.
    """
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt

    p1  = float(np.percentile(scores_2d,  1))
    p99 = float(np.percentile(scores_2d, 99))
    clipped = np.clip(scores_2d, p1, p99)
    log_s = np.log1p(clipped)
    lo, hi = float(log_s.min()), float(log_s.max())
    norm = (log_s - lo) / (hi - lo + 1e-10)

    cmap = mcolors.LinearSegmentedColormap.from_list("heatmap", cmap_stops, N=512)
    # WHY flipud: numpy rows go top→bottom but latitude increases bottom→top
    rgba = cmap(np.flipud(norm))
    plt.imsave(out_path, rgba)
    logger.info(
        "written %s  (%.0f KB)", out_path, out_path.stat().st_size / 1024
    )


def generate_all_images(artifacts_dir: Path, grid_size: int = DENSE_SIZE) -> None:
    """
    Generate one PNG per category + the "all" weighted blend.
    Also writes heatmap.png as a backward-compat alias for heatmap_all.png.
    """
    models, weights = _load_models(artifacts_dir)

    # ── "All" weighted blend ──────────────────────────────────────────────
    scores_2d = _score_dense_grid(models, weights, grid_size=grid_size)
    _scores_to_image(scores_2d, artifacts_dir / "heatmap_all.png", CATEGORY_CMAPS["all"])
    # Backward compat — existing backend config points at heatmap.png
    import shutil
    shutil.copy2(artifacts_dir / "heatmap_all.png", artifacts_dir / "heatmap.png")

    # ── Per-category ──────────────────────────────────────────────────────
    for display_name, slug in CATEGORY_SLUGS.items():
        if display_name not in models:
            logger.warning("no model for %r — skipping", display_name)
            continue
        cmap_stops = CATEGORY_CMAPS.get(slug, CATEGORY_CMAPS["all"])
        scores_2d = _score_dense_grid(
            models, weights, grid_size=grid_size, single_cat=display_name
        )
        _scores_to_image(scores_2d, artifacts_dir / f"heatmap_{slug}.png", cmap_stops)

    logger.info("all category heatmaps written to %s", artifacts_dir)


# ── GeoJSON helpers (unchanged from original) ────────────────────────────────

def _time_modifier_export(hour: int) -> float:
    return _time_modifier(hour)


def _score_grid(models: dict, weights: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lats = np.arange(LAT_MIN, LAT_MAX + GRID_STEP, GRID_STEP)
    lngs = np.arange(LNG_MIN, LNG_MAX + GRID_STEP, GRID_STEP)
    lat_grid, lng_grid = np.meshgrid(lats, lngs, indexing="ij")
    flat_lats = lat_grid.ravel()
    flat_lngs = lng_grid.ravel()
    points = np.vstack([flat_lats, flat_lngs])
    scores = np.zeros(points.shape[1])
    for cat, kde in models.items():
        w = weights.get(cat, 0.0)
        if w == 0.0:
            continue
        scores += kde(points) * w
    scores *= _time_modifier(HOUR)
    logger.info(
        "scored %d grid points  min=%.4f  max=%.4f  mean=%.4f",
        len(scores), scores.min(), scores.max(), scores.mean(),
    )
    return flat_lats, flat_lngs, scores


def _band(score: float) -> str:
    if score < BAND_LOW:
        return "Low"
    if score < BAND_HIGH:
        return "Medium"
    return "High"


def _normalise_scores(scores: np.ndarray) -> np.ndarray:
    p1, p99 = float(np.percentile(scores, 1)), float(np.percentile(scores, 99))
    clipped = np.clip(scores, p1, p99)
    log_s = np.log1p(clipped)
    lo, hi = log_s.min(), log_s.max()
    if hi == lo:
        return np.zeros_like(scores)
    return (log_s - lo) / (hi - lo)


def _to_geojson(lats: np.ndarray, lngs: np.ndarray, scores: np.ndarray) -> dict:
    norm = _normalise_scores(scores)
    features = []
    for lat, lng, score, s_norm in zip(lats, lngs, scores, norm):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(lng), 4), round(float(lat), 4)]},
            "properties": {
                "risk_band":  _band(float(score)),
                "score_norm": round(float(s_norm), 4),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def generate(artifacts_dir: Path) -> Path:
    """Generate GeoJSON + all category PNGs."""
    out_geojson = artifacts_dir / "heatmap.geojson"

    models, weights = _load_models(artifacts_dir)

    # GeoJSON (unchanged)
    lats, lngs, scores = _score_grid(models, weights)
    geojson = _to_geojson(lats, lngs, scores)
    with open(out_geojson, "w", encoding="utf-8") as f:
        json.dump(geojson, f, separators=(",", ":"))
    logger.info(
        "heatmap.geojson written: %.0f KB, %d features",
        out_geojson.stat().st_size / 1024, len(geojson["features"]),
    )

    # All category PNGs
    generate_all_images(artifacts_dir)

    return out_geojson


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate static risk heatmap images per category")
    parser.add_argument("--artifacts", type=Path, default=ARTIFACTS_DIR)
    parser.add_argument("--grid-size", type=int, default=DENSE_SIZE)
    args = parser.parse_args()
    generate(args.artifacts)
