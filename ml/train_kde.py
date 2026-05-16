# ml/train_kde.py
"""
Train per-category Gaussian KDE models on a cleaned crime snapshot and save
artifacts for use by the backend risk scoring service.

Expects a Parquet snapshot produced by ml/data/ingest.py — NOT raw JSON.
The cleaning pipeline lives exclusively in ingest.py. train_kde.py only
filters the already-clean data and fits models.

Usage (from repo root):
    # Use the most recent snapshot automatically (production / CI):
    python -m ml.train_kde --latest

    # Point at a specific snapshot (debugging / historical retrains):
    python -m ml.train_kde --snapshot ml/data/snapshots/crimes_2026-05-15.parquet

Artifacts written to ml/artifacts/kde_{category_slug}.pkl
One .pkl per KDE-eligible category.
"""

from __future__ import annotations

import argparse
import logging
import pickle
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import mlflow

# WHY absolute path from __file__: makes the tracking URI CWD-independent so
# train_kde.py, train_lightgbm.py, and evaluate.py all write to the same store
# regardless of which directory the user runs from.
_DB = Path(__file__).resolve().parents[1] / "ml" / "artifacts" / "mlruns.db"
mlflow.set_tracking_uri(f"sqlite:///{_DB.as_posix()}")
mlflow.set_experiment("kde_training")

from ml.data.category_mapping import (
    FEMALE_WEIGHTS,
    KDE_ELIGIBLE,
)
from ml.kde_model import FixedBandwidthKDE

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_ML_DIR       = Path(__file__).parent
ARTIFACTS_DIR = _ML_DIR / "artifacts"
SNAPSHOT_DIR  = _ML_DIR / "data" / "snapshots"

# ── KDE hyperparameters ───────────────────────────────────────────────────────
# Bandwidth validated by EDA Part 4 surface plots (~1.5 km at Delhi's latitude).
BANDWIDTH = 0.015   # degrees
# Recency half-life: exp(-age_days / HALF_LIFE).
# EDA Part 5 confirmed 83.1% of total weight comes from records ≤ 90 days old.
HALF_LIFE = 90      # days

# Hard minimum training points per category.
# Script exits non-zero if any category falls below this threshold.
MIN_TRAIN_POINTS = 50

# Required columns in the Parquet snapshot — fail fast if any are missing.
_REQUIRED_COLUMNS = {
    "crime_macro", "lat", "lng",
    "is_delhi_crime", "is_historical", "effective_date",
}


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot loading
# ─────────────────────────────────────────────────────────────────────────────

def find_latest_snapshot(snapshot_dir: Path = SNAPSHOT_DIR) -> Path:
    """Return the most recently dated crimes_*.parquet in snapshot_dir.

    Raises FileNotFoundError if no snapshots exist — which means ingest.py
    has not been run yet.
    """
    snapshots = sorted(snapshot_dir.glob("crimes_*.parquet"), reverse=True)
    if not snapshots:
        raise FileNotFoundError(
            f"No snapshots found in {snapshot_dir}. "
            "Run `python -m ml.data.ingest` (or `--from-json`) first."
        )
    latest = snapshots[0]
    log.info("latest snapshot: %s", latest.name)
    return latest


def load_snapshot(snapshot_path: Path) -> pd.DataFrame:
    """Load a cleaned Parquet snapshot and validate required columns exist.

    The snapshot is produced by ingest.py and is already fully cleaned.
    train_kde.py does NOT re-clean — it trusts the snapshot.
    """
    log.info("loading snapshot: %s", snapshot_path)
    df = pd.read_parquet(snapshot_path)
    log.info("snapshot loaded: %d records", len(df))

    missing = _REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"Snapshot is missing required columns: {missing}. "
            "Re-run ingest.py to regenerate the snapshot."
        )

    # Ensure datetime dtype — Parquet preserves it but be defensive.
    df["effective_date"] = pd.to_datetime(df["effective_date"], errors="coerce")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# KDE pool
# ─────────────────────────────────────────────────────────────────────────────

def build_kde_pool(df: pd.DataFrame) -> pd.DataFrame:
    """Filter the cleaned snapshot down to the KDE training pool.

    Filters (all must be True):
      - is_delhi_crime : location_broad contains 'Delhi'
      - lat not null   : valid coordinates (Bucket A already nulled by ingest)
      - not is_historical : year >= 2007
      - crime_macro in KDE_ELIGIBLE : excludes Fraud/Cybercrime, Other, Unknown
    """
    pool = df[
        df["is_delhi_crime"]
        & df["lat"].notna()
        & ~df["is_historical"]
        & df["crime_macro"].isin(KDE_ELIGIBLE)
    ].copy()
    log.info("KDE pool: %d records", len(pool))
    return pool


def print_pool_summary(pool: pd.DataFrame) -> None:
    """Print a per-category summary table to confirm pool matches EDA counts."""
    print("\n" + "=" * 62)
    print("  KDE Training Pool — Summary")
    print("=" * 62)
    print(f"  {'Category':<24} {'Weight':>7}  {'Count':>7}  {'Status'}")
    print("  " + "-" * 58)

    all_ok = True
    for cat in KDE_ELIGIBLE:
        count  = (pool["crime_macro"] == cat).sum()
        weight = FEMALE_WEIGHTS[cat]
        if count >= MIN_TRAIN_POINTS:
            status = "OK"
        elif count >= 10:
            status = "WARN (<50)"
            all_ok = False
        else:
            status = "FAIL (<10)"
            all_ok = False
        print(f"  {cat:<24} {weight:>7.1f}  {count:>7}  {status}")

    print("  " + "-" * 58)
    print(f"  {'TOTAL':<24} {'':>7}  {len(pool):>7}")
    print("=" * 62)

    has_date  = pool["effective_date"].notna().sum()
    null_date = pool["effective_date"].isna().sum()
    print(f"\n  effective_date coverage : {has_date} / {len(pool)} records")
    print(f"  null effective_date     : {null_date} records (weight=1.0 fallback)")

    if not all_ok:
        log.warning("one or more categories below minimum training threshold")


# ─────────────────────────────────────────────────────────────────────────────
# Recency weights
# ─────────────────────────────────────────────────────────────────────────────
# FixedBandwidthKDE imported from ml.kde_model — defined there so pickle
# serialises it as 'ml.kde_model.FixedBandwidthKDE', a stable importable path.

def compute_recency_weights(
    pool: pd.DataFrame,
    reference_date: pd.Timestamp,
) -> np.ndarray:
    """Compute per-record recency weights: exp(-age_days / HALF_LIFE).

    Records with null effective_date receive weight=1.0 (neutral fallback).
    EDA Part 5 confirmed this affects only 10 records (0.21% of pool) —
    well under the 5% threshold where it would meaningfully bias the KDE.

    Args:
        pool: KDE training pool DataFrame with effective_date column.
        reference_date: The date from which age is measured (today at train time).

    Returns:
        np.ndarray of shape (n,) with a weight per record.
    """
    age_days = (reference_date - pool["effective_date"]).dt.days

    # Null effective_date → NaN age → fallback to weight 1.0
    weights = np.where(
        age_days.isna(),
        1.0,
        np.exp(-age_days.fillna(0).values / HALF_LIFE),
    )

    null_count = age_days.isna().sum()
    if null_count > 0:
        log.info(
            "recency weights: %d null-date records assigned weight=1.0 (%.2f%% of pool)",
            null_count, null_count / len(pool) * 100,
        )

    return weights.astype(np.float64)


# ─────────────────────────────────────────────────────────────────────────────
# Category slug
# ─────────────────────────────────────────────────────────────────────────────

def category_slug(category: str) -> str:
    """Convert a macro category name to a filesystem-safe slug.

    Examples:
        'Sexual Violence'   → 'sexual_violence'
        'Terrorism / Riot'  → 'terrorism_riot'
        'Drug / Trafficking'→ 'drug_trafficking'
        'Theft / Burglary'  → 'theft_burglary'
    """
    # Lowercase, replace any run of non-alphanumeric chars with underscore,
    # strip leading/trailing underscores.
    slug = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
    return slug


# ─────────────────────────────────────────────────────────────────────────────
# KDE fitting and artifact saving
# ─────────────────────────────────────────────────────────────────────────────

def fit_and_save(
    pool: pd.DataFrame,
    reference_date: pd.Timestamp,
    artifacts_dir: Path,
) -> list[dict]:
    """Fit one KDE per category and save artifacts to artifacts_dir.

    For each KDE-eligible category:
      1. Extract lat/lng coordinates from the pool subset.
      2. Compute per-record recency weights.
      3. Fit scipy.stats.gaussian_kde with those weights.
      4. Save a .pkl artifact dict to artifacts_dir/kde_{slug}.pkl.

    scipy.stats.gaussian_kde expects dataset shape (2, n) — (lat_row, lng_row).
    The bw_method scalar sets the bandwidth directly in data units (degrees).

    Returns a list of summary dicts (one per category) for the training table.
    """
    weights_all = compute_recency_weights(pool, reference_date)
    trained_at  = datetime.now(timezone.utc).isoformat()
    summaries   = []

    for cat in KDE_ELIGIBLE:
        mask   = pool["crime_macro"] == cat
        subset = pool[mask].copy()
        w      = weights_all[mask.values]

        n_train = len(subset)
        log.info("fitting KDE for '%s': n=%d", cat, n_train)

        # Stack coordinates into shape (2, n) — scipy convention.
        # lat is row 0, lng is row 1. risk_model.py scores with the same order.
        coords = np.vstack([
            subset["lat"].values,
            subset["lng"].values,
        ])  # shape (2, n_train)

        # WHY FixedBandwidthKDE: passing a scalar float to gaussian_kde's
        # bw_method creates an internal lambda that breaks pickle.
        # FixedBandwidthKDE overrides covariance_factor() as a regular method,
        # making the object fully picklable. See class docstring for details.
        kde = FixedBandwidthKDE(coords, bandwidth=BANDWIDTH, weights=w)

        # Build artifact dict — matches the structure risk_model.py expects.
        artifact = {
            "kde"       : kde,
            "weight"    : FEMALE_WEIGHTS[cat],
            "category"  : cat,
            "n_train"   : n_train,
            "bandwidth" : BANDWIDTH,
            "half_life" : HALF_LIFE,
            "trained_at": trained_at,
        }

        slug      = category_slug(cat)
        out_path  = artifacts_dir / f"kde_{slug}.pkl"

        with open(out_path, "wb") as f:
            pickle.dump(artifact, f, protocol=pickle.HIGHEST_PROTOCOL)

        log.info("saved: %s", out_path.name)

        summaries.append({
            "category" : cat,
            "n_train"  : n_train,
            "bandwidth": BANDWIDTH,
            "artifact" : out_path.name,
        })

    return summaries


def print_training_summary(summaries: list[dict]) -> None:
    """Print the post-training summary table."""
    print("\n" + "=" * 72)
    print("  KDE Training — Complete")
    print("=" * 72)
    print(f"  {'Category':<24} {'n_train':>8}  {'Bandwidth':>10}  {'Artifact'}")
    print("  " + "-" * 68)
    for s in summaries:
        print(
            f"  {s['category']:<24} {s['n_train']:>8}  "
            f"{s['bandwidth']:>10.3f}  {s['artifact']}"
        )
    print("=" * 72)
    print(f"\n  Artifacts written to: {ARTIFACTS_DIR}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main(snapshot_path: Path) -> int:
    """Main entry point. Returns exit code (0 = success, 1 = failure)."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Load snapshot ─────────────────────────────────────────────────────────
    df = load_snapshot(snapshot_path)

    # ── Build KDE pool ────────────────────────────────────────────────────────
    kde_pool = build_kde_pool(df)

    # ── Print summary and validate minimums ───────────────────────────────────
    print_pool_summary(kde_pool)

    failed_cats = [
        cat for cat in KDE_ELIGIBLE
        if (kde_pool["crime_macro"] == cat).sum() < MIN_TRAIN_POINTS
    ]
    if failed_cats:
        log.error(
            "categories below MIN_TRAIN_POINTS=%d: %s — aborting",
            MIN_TRAIN_POINTS, failed_cats,
        )
        return 1

    # ── Fit KDE models, save artifacts, and log to MLflow ────────────────────
    # Reference date = now. All recency weights are computed relative to this.
    reference_date = pd.Timestamp.now(tz=None).normalize()
    log.info("reference date for recency weights: %s", reference_date.date())

    # Extract snapshot date from filename (crimes_YYYY-MM-DD.parquet).
    # Falls back to today's date if the filename doesn't match the pattern.
    snapshot_date_match = re.search(r"(\d{4}-\d{2}-\d{2})", snapshot_path.name)
    snapshot_date = (
        snapshot_date_match.group(1) if snapshot_date_match
        else reference_date.date().isoformat()
    )

    with mlflow.start_run() as active_run:
        mlflow.set_tags({
            "pipeline_step":  "train_kde",
            "snapshot_date":  snapshot_date,
        })

        mlflow.log_params({
            "bandwidth":         BANDWIDTH,
            "half_life":         HALF_LIFE,
            "min_train_points":  MIN_TRAIN_POINTS,
            "snapshot":          snapshot_path.name,
        })

        summaries = fit_and_save(kde_pool, reference_date, ARTIFACTS_DIR)

        # Per-category n_train metrics
        n_train_total = 0
        for s in summaries:
            slug = category_slug(s["category"])
            mlflow.log_metric(f"n_train_{slug}", s["n_train"])
            n_train_total += s["n_train"]

        mlflow.log_metric("n_train_total",  n_train_total)
        mlflow.log_metric("n_categories",   len(summaries))

        # Log all kde_*.pkl files as a group under "kde_artifacts/".
        # promote_model.py searches for this artifact path to find the run.
        mlflow.log_artifacts(str(ARTIFACTS_DIR), artifact_path="kde_artifacts")

        run_id = active_run.info.run_id

    log.info("MLflow run_id: %s", run_id)

    # ── Print training summary ────────────────────────────────────────────────
    print_training_summary(summaries)

    log.info("training complete — %d artifacts written to %s",
             len(summaries), ARTIFACTS_DIR)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Train KDE crime risk models from a cleaned Parquet snapshot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-discover the latest snapshot (used by CI / weekly retrain):
  python -m ml.train_kde --latest

  # Point at a specific snapshot (debugging / historical retrains):
  python -m ml.train_kde --snapshot ml/data/snapshots/crimes_2026-05-15.parquet
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--latest",
        action="store_true",
        help="Auto-discover and use the most recent snapshot in ml/data/snapshots/",
    )
    group.add_argument(
        "--snapshot",
        type=Path,
        metavar="PATH",
        help="Path to a specific crimes_*.parquet snapshot file",
    )
    args = parser.parse_args()

    if args.latest:
        try:
            snapshot_path = find_latest_snapshot()
        except FileNotFoundError as e:
            log.error("%s", e)
            sys.exit(1)
    else:
        snapshot_path = args.snapshot
        if not snapshot_path.exists():
            log.error("snapshot not found: %s", snapshot_path)
            sys.exit(1)

    sys.exit(main(snapshot_path))
