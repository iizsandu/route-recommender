"""
ml/build_edge_risk.py  —  Stage 2: Edge Risk Pipeline

Reads:
  ml/artifacts/gh_edges.csv    — GH edge geometries (produced by EdgeExporter in Stage 0)
  ml/artifacts/kde_*.pkl       — KDE density models per crime category

Writes:
  ml/artifacts/edge_risk.json  — crime risk scores keyed by GH integer edge ID

Usage:
  python -m ml.build_edge_risk
  python -m ml.build_edge_risk --gh-edges path/to/gh_edges.csv
  python -m ml.build_edge_risk --artifacts-dir path/to/kde/dir

The JSON is read by CrimeWeighting.java at GraphHopper startup.
Edge IDs are GH integers — no centroid matching, no locale issues.

Scoring method: KDE surface evaluated at each edge centroid.
The KDE models already encode the full spatial crime-density surface, so
evaluating them at a centroid is equivalent to asking "how dangerous is
the neighbourhood this road passes through?" — no raw crime records needed.
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
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


# ── Step 2: Load GH edge geometries ──────────────────────────────────────────

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


# ── Step 3: Score edges via KDE surface ───────────────────────────────────────

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
    re-read the raw crime records.

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


# ── Step 4: P99 normalisation ─────────────────────────────────────────────────

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


# ── Step 5: Write JSON ────────────────────────────────────────────────────────

def write_output(
    normalised: dict[int, float],
    n_edges_total: int,
    raw_scores: dict[int, float],
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
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "max_raw_score": float(max(raw_scores.values())) if raw_scores else 0.0,
            "p99_raw_score": float(np.percentile(list(raw_scores.values()), 99))
                             if raw_scores else 0.0,
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
            "  Check that kde_*.pkl files are present in artifacts dir.",
            coverage * 100, COVERAGE_THRESHOLD * 100,
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

    log.info("[3] Scoring edges via KDE surface at centroids ...")
    raw_scores = score_edges_via_kde_surface(edges_gdf, kde_models)

    log.info("[4] Normalising (P99 clip) and writing output ...")
    normalised = normalise_scores(raw_scores)
    write_output(
        normalised=normalised,
        n_edges_total=len(edges_gdf),
        raw_scores=raw_scores,
        gh_edges_path=args.gh_edges,
        out_path=ARTIFACTS_DIR / "edge_risk.json",
    )
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
