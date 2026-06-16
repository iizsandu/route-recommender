"""
ml/build_edge_risk.py  —  Stage 2: Edge Risk Pipeline

Reads:
  ml/artifacts/gh_edges.csv    — GH edge geometries (produced by EdgeExporter in Stage 0)
  ml/artifacts/kde_*.pkl       — KDE density models per crime category
  ml/data/snapshots/crimes_*.parquet — crime pool with lat/lng/category/date

Writes:
  ml/artifacts/edge_risk.json  — crime risk scores keyed by GH integer edge ID

Usage:
  python -m ml.build_edge_risk
  python -m ml.build_edge_risk --gh-edges path/to/gh_edges.csv
  python -m ml.build_edge_risk --snapshot path/to/crimes_DATE.parquet

The JSON is read by CrimeWeighting.java at GraphHopper startup.
Edge IDs are GH integers — no centroid matching, no locale issues.
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import wkt as shapely_wkt

# Repo root so ml.* imports work regardless of working directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from ml.data.category_mapping import FEMALE_WEIGHTS, KDE_ELIGIBLE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ARTIFACTS_DIR = _REPO_ROOT / "ml" / "artifacts"
COVERAGE_THRESHOLD = 0.10   # fail if < 10% of GH edges have a non-zero score
BUFFER_METRES = 150         # crime points within this radius of a road are attributed to it
HALF_LIFE_DAYS = 90         # recency decay: exp(-age_days / 90)


# ── Step 1: Load KDE models ───────────────────────────────────────────────────

def load_kde_models(artifacts_dir: Path) -> dict:
    """Return {category_string: FixedBandwidthKDE} for all kde_*.pkl files."""
    models = {}
    for pkl_path in sorted(artifacts_dir.glob("kde_*.pkl")):
        with open(pkl_path, "rb") as f:
            d = pickle.load(f)
        models[d["category"]] = d["kde"]
        log.info("  Loaded %s  n_train=%d  bandwidth=%.4f",
                 d["category"], d["n_train"], d["bandwidth"])
    if not models:
        raise FileNotFoundError(f"No kde_*.pkl files found in {artifacts_dir}")
    return models


# ── Step 2: Load crime pool from Parquet ─────────────────────────────────────

def load_crime_pool(snapshot_path: Path | None) -> pd.DataFrame:
    """Load and filter the Parquet snapshot to the KDE-eligible crime pool."""
    from ml.train_kde import build_kde_pool, find_latest_snapshot  # noqa: PLC0415
    if snapshot_path is None:
        snapshot_path = find_latest_snapshot()
    log.info("Loading snapshot: %s", snapshot_path.name)
    df = pd.read_parquet(snapshot_path)
    pool = build_kde_pool(df)
    log.info("  KDE pool: %d records", len(pool))
    return pool


def _validate_versions(pool: pd.DataFrame, kde_models: dict, snapshot_path: Path) -> None:
    """Warn if KDE models were trained on a different snapshot than we loaded."""
    snap_match = re.search(r"crimes_(\d{4}-\d{2}-\d{2})", snapshot_path.name)
    if not snap_match:
        return
    snap_date = datetime.fromisoformat(snap_match.group(1)).replace(tzinfo=timezone.utc)
    # We can't easily get trained_at from kde_models dict (we only stored the kde object).
    # This check is informational only — no blocking.
    log.info("  Snapshot date: %s", snap_match.group(1))


# ── Step 3: Load GH edge geometries ──────────────────────────────────────────

def load_gh_edges(gh_edges_path: Path) -> gpd.GeoDataFrame:
    """
    Read gh_edges.csv produced by EdgeExporter.
    Columns: edge_id (int), length_m (float), geom_wkt (WKT LINESTRING lng lat).
    Returns a GeoDataFrame in EPSG:4326.

    WHY not pd.read_csv: the geom_wkt field is an unquoted LINESTRING whose
    coordinate pairs are comma-separated (e.g. "LINESTRING(77.1 28.1,77.2 28.2)").
    pandas splits on every comma, producing too many columns. Splitting each line
    on the first two commas only reconstructs the three-field structure correctly.
    """
    log.info("Loading GH edge geometries: %s", gh_edges_path)
    with open(gh_edges_path, encoding="utf-8") as f:
        next(f)  # skip header: edge_id,length_m,geom_wkt
        records = [line.split(",", 2) for line in f]

    df = pd.DataFrame(records, columns=["edge_id", "length_m", "geom_wkt"])
    df["geom_wkt"]  = df["geom_wkt"].str.rstrip("\n")
    df["edge_id"]   = df["edge_id"].astype(int)
    df["length_m"]  = df["length_m"].astype(float)
    log.info("  GH edges: %d", len(df))

    # geom_wkt is "LINESTRING(lng lat, ...)" — WKT standard (x=lng, y=lat).
    # shapely_wkt.loads() returns Shapely geometry; Point.x=lng, Point.y=lat.
    df["geometry"] = df["geom_wkt"].apply(shapely_wkt.loads)
    gdf = gpd.GeoDataFrame(df[["edge_id", "length_m", "geometry"]], crs="EPSG:4326")
    return gdf


# ── Step 4: Spatial matching ──────────────────────────────────────────────────

def match_crimes_to_edges(
    pool: pd.DataFrame,
    edges_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Buffer each GH edge LineString by BUFFER_METRES, spatial-join crime points.
    Returns a DataFrame with columns: [crime index cols] + edge_id.

    WHY buffer the full LineString (not midpoints): a 500m road segment has
    crime incidents distributed along its length; buffering the midpoint misses
    incidents at the far ends. LineString.buffer() creates a rounded corridor.

    WHY EPSG:32643 (UTM Zone 43N): buffering in EPSG:4326 produces ellipses —
    a 150m buffer becomes ~130m in the longitude direction at 28.6°N. UTM 43N
    makes both axes equal in metres.
    """
    # Project to UTM Zone 43N (Delhi is at ~77°E, well within zone 43N: 72–78°E)
    edges_proj = edges_gdf.to_crs("EPSG:32643")

    # Build crime GeoDataFrame. pool has lat/lng columns.
    # gpd.points_from_xy takes (x=lng, y=lat) → stores as Point(lng, lat) in EPSG:4326.
    crime_geoms = gpd.points_from_xy(pool["lng"], pool["lat"])
    crime_gdf = gpd.GeoDataFrame(pool.copy(), geometry=crime_geoms, crs="EPSG:4326")
    crimes_proj = crime_gdf.to_crs("EPSG:32643")

    # Buffer the full edge LineStrings by 150m (accurate metres in UTM).
    edge_buffers = edges_proj.copy()
    edge_buffers["geometry"] = edges_proj.geometry.buffer(BUFFER_METRES)
    # Reset index to a simple integer so sjoin produces a single "index_right" column
    # (not a MultiIndex). Without reset_index(), GeoPandas can produce index_right0/1/2
    # if the right GeoDataFrame has a non-trivial index.
    edge_buffers = edge_buffers.reset_index(drop=True)

    log.info("  Running spatial join (150m buffer, %d crime points × %d edges)...",
             len(crimes_proj), len(edge_buffers))

    joined = crimes_proj.sjoin(
        edge_buffers[["edge_id", "geometry"]],
        how="left",
        predicate="within",
    )
    # "index_right" is the integer row index of edge_buffers, which has "edge_id" as a column.
    matched = joined.dropna(subset=["index_right"])
    log.info("  Matched: %d crime-edge pairs  |  Discarded: %d crime points",
             len(matched), len(joined) - len(matched))
    return matched


# ── Step 5: Accumulate risk scores per edge ───────────────────────────────────

def _recency_weight(effective_date, today: datetime) -> float:
    """exp(-age_days / 90). Null dates treated as weight=1.0 (same as KDE training)."""
    if pd.isna(effective_date):
        return 1.0
    try:
        age_days = max(0, (today - effective_date.replace(tzinfo=timezone.utc)).days)
    except (AttributeError, TypeError):
        return 1.0
    return float(np.exp(-age_days / HALF_LIFE_DAYS))


def accumulate_scores(
    matched: pd.DataFrame,
    kde_models: dict,
) -> dict[int, float]:
    """
    For each matched (crime_point, edge) pair, accumulate:
        kde_density(c.lat, c.lng) × female_weight(c.category) × recency_weight(c.date)

    WHY evaluate KDE at the crime point's own location (not the edge midpoint):
    A crime in a dense crime cluster evaluates to a higher KDE density, amplifying
    the contribution of crimes that occur where many other similar crimes happened.
    This makes the edge risk a function of both the crime's recency/type AND the
    overall dangerousness of that neighbourhood.

    WHY a crime within 150m of multiple edges contributes to all of them:
    A crime in a market square surrounded by four roads represents real risk on
    all four roads. Attributing to the nearest road only would create discontinuities.
    """
    today = datetime.now(timezone.utc)
    edge_raw_scores: dict[int, float] = defaultdict(float)

    for _, row in matched.iterrows():
        cat = row.get("crime_macro")
        if cat not in kde_models or cat not in FEMALE_WEIGHTS:
            continue
        eid = int(row["edge_id"])

        # KDE expects shape (2, 1): row 0 = lat, row 1 = lng.
        # Confirmed from pkl inspection: kde.dataset[0] = latitudes.
        density = float(kde_models[cat](np.array([[row.lat], [row.lng]]))[0])
        fw      = FEMALE_WEIGHTS[cat]
        rw      = _recency_weight(row.get("effective_date"), today)
        edge_raw_scores[eid] += density * fw * rw

    log.info("  Edges with at least one crime: %d", len(edge_raw_scores))
    return dict(edge_raw_scores)


# ── Step 5b: KDE-at-centroid (no snapshot needed) ────────────────────────────

def score_edges_via_kde_surface(
    edges_gdf: gpd.GeoDataFrame,
    kde_models: dict,
    batch_size: int = 50_000,
) -> dict[int, float]:
    """
    Score each GH edge by evaluating the KDE crime-density surface at the
    edge centroid. No crime snapshot parquet required.

    WHY centroid approach: the KDE models already encode the full spatial
    crime density surface — evaluating them at an edge centroid is equivalent
    to asking "how dense is crime near this road segment?" without needing to
    re-read the raw crime records. Suitable when a fresh snapshot isn't available.

    WHY batched: scipy gaussian_kde.evaluate() is O(N×M) where N = training
    points and M = query points. Processing 546K edges at once allocates a
    ~4 GB matrix. Batches of 50K keep peak RAM under 400 MB.
    """
    centroids = edges_gdf.geometry.centroid  # GeoSeries of Points; .x=lng, .y=lat
    lat_arr   = centroids.y.values
    lng_arr   = centroids.x.values
    points    = np.vstack([lat_arr, lng_arr])  # (2, n_edges) — KDE convention
    n         = len(edges_gdf)

    log.info("  lat [%.3f, %.3f]  lng [%.3f, %.3f]",
             lat_arr.min(), lat_arr.max(), lng_arr.min(), lng_arr.max())

    weighted = np.zeros(n, dtype=np.float64)
    for cat, kde in kde_models.items():
        w = FEMALE_WEIGHTS.get(cat, 0.5)
        if w == 0:
            continue
        log.info("  %s (weight=%.1f) ...", cat, w)
        cat_scores = np.empty(n, dtype=np.float64)
        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            cat_scores[start:end] = kde.evaluate(points[:, start:end])
        weighted += w * cat_scores
        log.info("    max density = %.4e", cat_scores.max())

    edge_ids = edges_gdf["edge_id"].values.astype(int)
    return {int(eid): float(s) for eid, s in zip(edge_ids, weighted)}


# ── Step 6: P99 normalisation ─────────────────────────────────────────────────

def normalise_scores(raw_scores: dict[int, float]) -> dict[int, float]:
    """
    Normalise to [0, 1] using the 99th percentile as the ceiling.

    WHY P99 not max: a single extreme crime cluster (e.g. a police station with
    dozens of registered FIRs) can produce a maximum raw score that is 10× higher
    than the next-highest edge. Dividing by that maximum compresses all other edges
    toward zero, making lambda calibration unstable between retrains. P99 clipping
    allows the top 1% of extreme edges to saturate at 1.0 while keeping the rest
    of the distribution well-spread.
    """
    if not raw_scores:
        log.warning("No edges scored — edge_risk.json will be empty.")
        return {}

    values = np.array(list(raw_scores.values()))
    p99 = float(np.percentile(values, 99))
    denom = max(p99, 1e-10)  # guard against degenerate all-zero case
    log.info("  Max raw score: %.6f  |  P99 raw score: %.6f", values.max(), p99)

    normalised = {}
    for eid, raw in raw_scores.items():
        # min(..., 1.0) caps the ~1% of edges that are above p99
        normalised[eid] = float(min(raw / denom, 1.0))
    return normalised


# ── Step 7: Write JSON ────────────────────────────────────────────────────────

def write_output(
    normalised: dict[int, float],
    n_edges_total: int,
    n_crime_points: int,
    raw_scores: dict[int, float],
    snapshot_path: Path,
    gh_edges_path: Path,
    out_path: Path,
) -> None:
    n_edges_scored = sum(1 for v in normalised.values() if v > 0)
    coverage = n_edges_scored / n_edges_total if n_edges_total > 0 else 0

    output = {
        # Keys are GH integer edge IDs stored as strings (JSON keys must be strings).
        # Java parses them back with Long.parseLong(key).
        "edge_scores": {str(eid): score for eid, score in normalised.items()},
        "metadata": {
            "n_edges_scored": n_edges_scored,
            "n_edges_total": n_edges_total,
            "n_crime_points": n_crime_points,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "max_raw_score": float(max(raw_scores.values())) if raw_scores else 0.0,
            "p99_raw_score": float(np.percentile(list(raw_scores.values()), 99))
                             if raw_scores else 0.0,
            "snapshot_date": re.search(r"crimes_(\d{4}-\d{2}-\d{2})",
                                       snapshot_path.name).group(1)
                             if snapshot_path else "unknown",
            # Docker-entrypoint.sh compares this to actual CSV row count to detect
            # a stale JSON built from a different graph version.
            "gh_edges_csv_rows": n_edges_total,
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    log.info("Written: %s", out_path)
    log.info("  Coverage: %d / %d edges (%.1f%%)", n_edges_scored, n_edges_total, coverage * 100)

    if coverage < COVERAGE_THRESHOLD:
        log.error(
            "FAIL: non-zero risk coverage %.1f%% < %.0f%% threshold.\n"
            "  Try widening BUFFER_METRES from %d to 250, or investigate the crime pool filter.",
            coverage * 100, COVERAGE_THRESHOLD * 100, BUFFER_METRES,
        )
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Build edge risk scores for GraphHopper.")
    parser.add_argument("--gh-edges", type=Path,
                        default=ARTIFACTS_DIR / "gh_edges.csv",
                        help="Path to gh_edges.csv produced by EdgeExporter.")
    parser.add_argument("--artifacts-dir", type=Path,
                        default=ARTIFACTS_DIR,
                        help="Directory containing kde_*.pkl files.")
    parser.add_argument("--snapshot", type=Path,
                        default=None,
                        help="Path to a specific crimes_*.parquet snapshot.")
    parser.add_argument("--kde-only", action="store_true",
                        help="Score edges by evaluating KDE at centroids — no snapshot needed.")
    args = parser.parse_args()

    if not args.gh_edges.exists():
        log.error("gh_edges.csv not found at %s", args.gh_edges)
        log.error("Run Stage 0: docker exec <container> java -cp /app/graphhopper.jar "
                  "com.graphhopper.tools.EdgeExporter /graphhopper/graph-cache /data/gh_edges.csv")
        sys.exit(1)

    log.info("=== Stage 2: Edge Risk Pipeline ===")

    log.info("[1] Loading KDE models from %s ...", args.artifacts_dir)
    kde_models = load_kde_models(args.artifacts_dir)

    log.info("[2] Loading GH edge geometries ...")
    edges_gdf = load_gh_edges(args.gh_edges)

    if args.kde_only:
        # ── KDE-at-centroid path (no parquet required) ────────────────────────
        log.info("[3] Scoring edges via KDE surface at centroids (--kde-only) ...")
        raw_scores = score_edges_via_kde_surface(edges_gdf, kde_models)
        snap_path  = None
        n_crimes   = 0
    else:
        # ── Full spatial-join path (requires crime snapshot parquet) ──────────
        log.info("[3] Loading crime snapshot ...")
        from ml.train_kde import find_latest_snapshot  # noqa: PLC0415
        pool      = load_crime_pool(args.snapshot)
        snap_path = args.snapshot or find_latest_snapshot()
        _validate_versions(pool, kde_models, snap_path)
        n_crimes  = len(pool)

        log.info("[4] Spatial matching: crime points → GH edges (150m buffer) ...")
        matched = match_crimes_to_edges(pool, edges_gdf)

        log.info("[5] Accumulating risk scores ...")
        raw_scores = accumulate_scores(matched, kde_models)

    log.info("[final] Normalising (P99 clip) and writing output ...")
    normalised = normalise_scores(raw_scores)
    write_output(
        normalised=normalised,
        n_edges_total=len(edges_gdf),
        n_crime_points=n_crimes,
        raw_scores=raw_scores,
        snapshot_path=snap_path,
        gh_edges_path=args.gh_edges,
        out_path=ARTIFACTS_DIR / "edge_risk.json",
    )
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
