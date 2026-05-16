# ml/evaluate.py
#
# Time-based holdout evaluation for the KDE risk model.
# Splits a crime snapshot at (today - 30 days), scores a grid of Delhi-NCR
# cells with the trained KDE, and reports PR-AUC + recall@10%.
# All metrics are logged to MLflow so train.py and evaluate.py runs are linked.

from __future__ import annotations

import logging
import pickle
from datetime import date, timedelta
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde  # noqa: F401 — needed for unpickling
from sklearn.metrics import average_precision_score

from ml.kde_model import FixedBandwidthKDE  # noqa: F401 — needed for unpickling

logger = logging.getLogger(__name__)

_DB = Path(__file__).resolve().parents[1] / "ml" / "artifacts" / "mlruns.db"
mlflow.set_tracking_uri(f"sqlite:///{_DB.as_posix()}")
mlflow.set_experiment("kde_evaluation")

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"

# Geographic grid covering Delhi-NCR.
# 0.05° ≈ 5.5 km — coarse enough to have >0 crimes per cell,
# fine enough to distinguish risky corridors from safe ones.
LAT_MIN, LAT_MAX = 28.0, 29.5
LNG_MIN, LNG_MAX = 76.5, 78.0
GRID_STEP = 0.05

HOLDOUT_DAYS = 30


# ── Data split ────────────────────────────────────────────────────────────────

def _split_by_date(
    df: pd.DataFrame, cutoff: date
) -> tuple[pd.DataFrame, pd.DataFrame]:
    # WHY errors="coerce": effective_date may be null or badly formatted;
    # coerce → NaT, which then falls to the train set (conservative default).
    dates = pd.to_datetime(df["effective_date"], errors="coerce").dt.date
    is_test = dates.ge(cutoff).fillna(False)
    return df[~is_test].copy(), df[is_test].copy()


# ── Grid helpers ──────────────────────────────────────────────────────────────

def _make_grid() -> tuple[np.ndarray, np.ndarray]:
    # WHY indexing="ij": produces lats varying on axis-0, lngs on axis-1.
    # After ravel(), flat_idx = lat_idx * n_lng + lng_idx — used in _label_grid.
    lat_centers = np.arange(LAT_MIN, LAT_MAX, GRID_STEP) + GRID_STEP / 2
    lng_centers = np.arange(LNG_MIN, LNG_MAX, GRID_STEP) + GRID_STEP / 2
    lats, lngs = np.meshgrid(lat_centers, lng_centers, indexing="ij")
    return lats.ravel(), lngs.ravel()


def _crime_to_flat_idx(
    crime_lats: np.ndarray, crime_lngs: np.ndarray, n_lng: int
) -> np.ndarray:
    # WHY floor then cast: np.floor gives the lower-left cell edge correctly
    # even for coords that land exactly on a boundary.
    n_lat = int((LAT_MAX - LAT_MIN) / GRID_STEP)
    lat_idx = np.floor((crime_lats - LAT_MIN) / GRID_STEP).astype(int)
    lng_idx = np.floor((crime_lngs - LNG_MIN) / GRID_STEP).astype(int)
    valid = (
        (lat_idx >= 0) & (lat_idx < n_lat) &
        (lng_idx >= 0) & (lng_idx < n_lng)
    )
    return lat_idx[valid] * n_lng + lng_idx[valid]


def _label_grid(test_df: pd.DataFrame, n_cells: int) -> np.ndarray:
    # Each cell gets label=1 if ≥1 test crime falls inside it.
    n_lng = int((LNG_MAX - LNG_MIN) / GRID_STEP)
    labels = np.zeros(n_cells, dtype=int)
    clean = test_df.dropna(subset=["lat", "lng"])
    flat_idx = _crime_to_flat_idx(clean["lat"].values, clean["lng"].values, n_lng)
    # WHY add.at: safe scatter-add handles duplicate indices (multiple crimes
    # in one cell) without losing counts — labels are then clamped to 1 below.
    np.add.at(labels, flat_idx, 1)
    return (labels > 0).astype(int)


# ── Artifact loading ──────────────────────────────────────────────────────────

def _load_model_from_dir(artifacts_dir: Path) -> dict:
    """Load all kde_*.pkl files from artifacts_dir and assemble a model payload.

    Returns a dict with the structure expected by _score_grid and
    _log_likelihood_test:
        {
            "models":  {category: FixedBandwidthKDE, ...},
            "weights": {category: float, ...},
        }

    WHY glob kde_*.pkl: train_kde.py writes one file per KDE-eligible category.
    Loading them all here keeps evaluate.py decoupled from the category list.
    """
    pkl_files = sorted(artifacts_dir.glob("kde_*.pkl"))
    if not pkl_files:
        raise FileNotFoundError(
            f"No kde_*.pkl files found in {artifacts_dir}. "
            "Run train_kde.py before evaluating."
        )

    models: dict[str, gaussian_kde] = {}
    weights: dict[str, float] = {}

    for pkl_path in pkl_files:
        with open(pkl_path, "rb") as f:
            artifact = pickle.load(f)
        cat = artifact["category"]
        models[cat]  = artifact["kde"]
        weights[cat] = artifact["weight"]
        logger.debug("loaded artifact: %s (category=%s)", pkl_path.name, cat)

    logger.info(
        "loaded %d per-category KDE artifacts from %s",
        len(models), artifacts_dir,
    )
    return {"models": models, "weights": weights}


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_grid(model_payload: dict, lats: np.ndarray, lngs: np.ndarray) -> np.ndarray:
    models: dict[str, gaussian_kde] = model_payload["models"]
    weights: dict[str, float] = model_payload["weights"]

    # WHY vstack shape (2, n): scipy gaussian_kde.__call__ expects (dims, pts).
    grid_pts = np.vstack([lats, lngs])
    scores = np.zeros(len(lats))

    for macro, kde in models.items():
        w = weights.get(macro, 0.5)
        # WHY no time_modifier: we're comparing spatial distributions, not
        # absolute magnitudes. Time modifier is constant across all cells so
        # it cancels out in the ranking used by PR-AUC and recall@10%.
        scores += w * kde(grid_pts)

    return scores


def _naive_density_scores(train_df: pd.DataFrame, n_cells: int) -> np.ndarray:
    # Baseline: raw crime counts per cell from the training period.
    # No KDE smoothing, no recency weighting, no female-focused weights.
    # A good model should beat this.
    n_lng = int((LNG_MAX - LNG_MIN) / GRID_STEP)
    counts = np.zeros(n_cells, dtype=float)
    clean = train_df.dropna(subset=["lat", "lng"])
    flat_idx = _crime_to_flat_idx(clean["lat"].values, clean["lng"].values, n_lng)
    np.add.at(counts, flat_idx, 1.0)
    return counts


# ── Metrics ───────────────────────────────────────────────────────────────────

def _recall_at_top_k(
    scores: np.ndarray, labels: np.ndarray, k_frac: float = 0.10
) -> float:
    # Of all actually-positive cells, what fraction land in the top-k% by score?
    # WHY top-k rather than threshold: avoids needing to pick an absolute cutoff,
    # which is meaningful only once we know the score distribution at deploy time.
    n_positives = int(labels.sum())
    if n_positives == 0:
        return float("nan")
    n_top = max(1, int(len(scores) * k_frac))
    top_idx = np.argsort(scores)[-n_top:]
    return float(labels[top_idx].sum() / n_positives)


def _log_likelihood_test(model_payload: dict, test_df: pd.DataFrame) -> float:
    # Sum of log(weighted KDE density) at each test crime location.
    # Higher is better — a model that assigns higher density to where crimes
    # actually occurred has a better log-likelihood.
    models: dict[str, gaussian_kde] = model_payload["models"]
    weights: dict[str, float] = model_payload["weights"]

    clean = test_df.dropna(subset=["lat", "lng"])
    if clean.empty:
        return float("nan")

    pts = clean[["lat", "lng"]].values.T  # shape (2, n)
    combined = np.zeros(pts.shape[1])
    for macro, kde in models.items():
        w = weights.get(macro, 0.5)
        combined += w * kde(pts)

    # WHY 1e-10 floor: prevents log(0) when a test point falls in a
    # region with zero training density (possible near bounding-box edges).
    return float(np.log(combined + 1e-10).sum())


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    snapshot_path: Path,
    artifacts_dir: Path = ARTIFACTS_DIR,
) -> dict[str, float | int]:
    df = pd.read_parquet(snapshot_path)
    logger.info("loaded %d records from %s", len(df), snapshot_path)

    cutoff = date.today() - timedelta(days=HOLDOUT_DAYS)
    train_df, test_df = _split_by_date(df, cutoff)
    logger.info(
        "split at %s → train=%d, test=%d", cutoff, len(train_df), len(test_df)
    )

    if test_df.empty:
        raise ValueError(
            f"test set is empty — no records after {cutoff}. "
            "Snapshot may be too old or HOLDOUT_DAYS is too large."
        )

    model_payload = _load_model_from_dir(artifacts_dir)

    lats, lngs = _make_grid()
    n_cells = len(lats)

    labels = _label_grid(test_df, n_cells)
    scores = _score_grid(model_payload, lats, lngs)

    n_positive = int(labels.sum())
    if n_positive == 0:
        raise ValueError(
            "no test crimes mapped to any grid cell — "
            "check Delhi-NCR bounding box and coordinate quality."
        )

    pr_auc     = float(average_precision_score(labels, scores))
    recall_10  = _recall_at_top_k(scores, labels, k_frac=0.10)
    log_lik    = _log_likelihood_test(model_payload, test_df)

    # ── Baselines ────────────────────────────────────────────────────────────
    rng = np.random.default_rng(seed=42)
    random_scores  = rng.random(n_cells)
    random_pr_auc  = float(average_precision_score(labels, random_scores))
    random_r10     = _recall_at_top_k(random_scores, labels)

    naive_scores   = _naive_density_scores(train_df, n_cells)
    naive_pr_auc   = float(average_precision_score(labels, naive_scores))
    naive_r10      = _recall_at_top_k(naive_scores, labels)

    metrics: dict[str, float | int] = {
        # Primary metrics
        "pr_auc":                     pr_auc,
        "recall_at_10pct":            recall_10,
        "log_likelihood_test":        log_lik,
        # Baselines
        "baseline_random_pr_auc":     random_pr_auc,
        "baseline_random_recall_10":  random_r10,
        "baseline_naive_pr_auc":      naive_pr_auc,
        "baseline_naive_recall_10":   naive_r10,
        # Bookkeeping
        "n_train":                    len(train_df),
        "n_test":                     len(test_df),
        "n_grid_cells":               n_cells,
        "n_positive_cells":           n_positive,
        "holdout_days":               HOLDOUT_DAYS,
    }

    with mlflow.start_run():
        mlflow.log_params({
            "snapshot":       snapshot_path.name,
            "artifacts_dir":  str(artifacts_dir),
            "cutoff_date":    cutoff.isoformat(),
            "grid_step_deg":  GRID_STEP,
            "holdout_days":   HOLDOUT_DAYS,
        })
        for k, v in metrics.items():
            mlflow.log_metric(k, float(v) if not isinstance(v, float) else v)

    logger.info(
        "PR-AUC  KDE=%.4f  naive=%.4f  random=%.4f",
        pr_auc, naive_pr_auc, random_pr_auc,
    )
    logger.info(
        "recall@10%%  KDE=%.4f  naive=%.4f  random=%.4f",
        recall_10, naive_r10, random_r10,
    )
    logger.info("log-likelihood on test set: %.2f", log_lik)

    return metrics


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 3:
        print("usage: python -m ml.evaluate <snapshot.parquet> <artifacts_dir>")
        sys.exit(1)

    result = run(Path(sys.argv[1]), Path(sys.argv[2]))
    print("\n-- Evaluation results --")
    for k, v in result.items():
        print(f"  {k:40s} {v}")
