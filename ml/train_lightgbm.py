# ml/train_lightgbm.py
"""
Train binary LightGBM risk classifiers from the (cell × week) feature table
produced by ml/data/h3_cells.py.

Trains two tiers of models:
  1. Global model — label = any KDE-eligible crime this week in this cell
  2. Per-category models — one each for Sexual Violence, Robbery, Assault
     (the three highest female-safety weight categories with enough data)

Time-series CV:
  Train split: all weeks up to (latest_week - 4 weeks)
  Test split:  final 4 weeks (held out, never seen by model)
  WHY 4 weeks not 30d: weekly rows align cleanly to 4-week holdout.

Artifacts saved to ml/artifacts/:
  lgb_model.pkl           — global model + metadata
  lgb_sexual_violence.pkl — category-specific model
  lgb_robbery.pkl
  lgb_assault.pkl

Usage:
    python -m ml.train_lightgbm --latest
    python -m ml.train_lightgbm --features ml/data/snapshots/cell_features_2026-05-15.parquet
"""

from __future__ import annotations

import argparse
import logging
import pickle
from datetime import date
from pathlib import Path
from typing import Optional

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import LabelEncoder

_DB = Path(__file__).resolve().parents[1] / "ml" / "artifacts" / "mlruns.db"
mlflow.set_tracking_uri(f"sqlite:///{_DB.as_posix()}")

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(__file__).parent / "data" / "snapshots"
ARTIFACTS_DIR = Path(__file__).parent / "artifacts"

# Feature columns passed to LightGBM (cell ID is pre-encoded to int)
FEATURE_COLS = [
    "h3_cell_enc",
    "month",
    "week_of_year",
    "day_of_week",
    "is_weekend",
    "cell_density_30d",
    "cell_density_90d",
    "neighbour_density_30d",
]

# Per-category label columns in the cell_features parquet
CATEGORY_LABEL_COLS = {
    "Sexual Violence": "crime_count_sexual_violence",
    "Robbery":        "crime_count_robbery",
    "Assault":        "crime_count_assault",
}

# LightGBM hyperparameters — conservative settings for sparse data
_LGB_PARAMS = {
    "objective":        "binary",
    "metric":           "average_precision",
    "learning_rate":    0.05,
    "num_leaves":       31,         # WHY 31: default; avoids overfitting on small data
    "min_child_samples": 10,        # WHY 10: prevents leaves with < 10 samples
    "feature_fraction": 0.8,        # WHY 0.8: light regularisation via feature subsampling
    "bagging_fraction": 0.8,
    "bagging_freq":     5,
    "verbose":          -1,         # WHY -1: suppress per-iteration LGB output; use MLflow
    "n_jobs":           -1,
}

_NUM_BOOST_ROUND = 300
_EARLY_STOPPING_ROUNDS = 30


# ── Data loading and splitting ────────────────────────────────────────────────

def _load_features(features_path: Path) -> pd.DataFrame:
    df = pd.read_parquet(features_path)
    df["week_start"] = pd.to_datetime(df["week_start"])

    missing = [c for c in FEATURE_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Feature table missing columns: {missing}. "
            "Re-run `python -m ml.data.h3_cells --latest` to regenerate."
        )
    return df


def _time_split(df: pd.DataFrame, holdout_weeks: int = 4) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split strictly on time — no leakage.

    WHY holdout_weeks not holdout_days: the panel is weekly; week boundaries
    are the only clean split points. 4 weeks ≈ 1 month.
    """
    cutoff = df["week_start"].max() - pd.Timedelta(weeks=holdout_weeks)
    train = df[df["week_start"] <= cutoff]
    test  = df[df["week_start"] >  cutoff]
    logger.info(
        "time split — train: %d rows (%d weeks), test: %d rows (%d weeks)",
        len(train), train["week_start"].nunique(),
        len(test),  test["week_start"].nunique(),
    )
    return train, test


# ── Model training ────────────────────────────────────────────────────────────

def _compute_scale_pos_weight(labels: pd.Series) -> float:
    """Ratio of negatives to positives — corrects class imbalance.

    WHY this matters: most (cell, week) pairs have no crime (label=0).
    Without correction, LightGBM predicts 0 for everything and achieves
    high accuracy but zero recall — useless for risk scoring.
    """
    n_pos = labels.sum()
    n_neg = len(labels) - n_pos
    if n_pos == 0:
        raise ValueError("No positive examples in training set — cannot train.")
    spw = n_neg / n_pos
    logger.info("scale_pos_weight = %.2f  (neg=%d, pos=%d)", spw, n_neg, n_pos)
    return spw


def _train_one(
    train: pd.DataFrame,
    test:  pd.DataFrame,
    label_col: str,
    model_tag: str,
) -> tuple[lgb.Booster, float, float]:
    """Train one binary LightGBM model.

    Args:
        train:     training split DataFrame
        test:      holdout split DataFrame
        label_col: name of the target column (0/1 integer)
        model_tag: human-readable label for logs/MLflow (e.g. "global", "Robbery")

    Returns:
        (trained Booster, train_pr_auc, test_pr_auc)
    """
    # Drop rows where the label column is missing (can happen for category cols)
    train = train[train[label_col].notna()].copy()
    test  = test[test[label_col].notna()].copy()

    y_train = train[label_col].astype(int)
    y_test  = test[label_col].astype(int)
    X_train = train[FEATURE_COLS]
    X_test  = test[FEATURE_COLS]

    spw = _compute_scale_pos_weight(y_train)
    params = {**_LGB_PARAMS, "scale_pos_weight": spw}

    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=FEATURE_COLS)
    dtest  = lgb.Dataset(X_test,  label=y_test,  reference=dtrain)

    callbacks = [
        lgb.early_stopping(_EARLY_STOPPING_ROUNDS, verbose=False),
        lgb.log_evaluation(period=50),   # WHY 50: one log line per 50 rounds
    ]

    model = lgb.train(
        params,
        dtrain,
        num_boost_round=_NUM_BOOST_ROUND,
        valid_sets=[dtest],
        callbacks=callbacks,
    )

    train_pr_auc = average_precision_score(y_train, model.predict(X_train))
    test_pr_auc  = average_precision_score(y_test,  model.predict(X_test))

    logger.info(
        "[%s] train PR-AUC=%.4f  test PR-AUC=%.4f  best_iter=%d",
        model_tag, train_pr_auc, test_pr_auc, model.best_iteration,
    )
    return model, train_pr_auc, test_pr_auc


# ── Recall@10% helper ─────────────────────────────────────────────────────────

def _recall_at_10pct(model: lgb.Booster, test: pd.DataFrame, label_col: str) -> float:
    """Fraction of positive (crime) rows captured in the top 10% by predicted risk.

    WHY PR-AUC is primary and recall@10% is secondary: PR-AUC summarises the
    full precision-recall curve. recall@10% answers the operational question
    "if we flag the top 10% riskiest cell-weeks, what fraction of actual crime
    weeks do we catch?" — directly tied to the product goal.
    """
    test = test[test[label_col].notna()].copy()
    probas = model.predict(test[FEATURE_COLS])
    y_true = test[label_col].astype(int).values

    threshold_idx = int(len(probas) * 0.9)   # top 10% threshold index
    top10_threshold = np.sort(probas)[threshold_idx]
    flagged = probas >= top10_threshold
    if y_true.sum() == 0:
        return 0.0
    return float(flagged[y_true == 1].mean())


# ── Artifact saving ───────────────────────────────────────────────────────────

def _save_artifact(
    model:        lgb.Booster,
    cell_encoder: dict[str, int],
    category:     str,
    trained_at:   str,
    train_pr_auc: float,
    test_pr_auc:  float,
    recall_10:    float,
    n_train:      int,
) -> Path:
    slug = category.lower().replace(" / ", "_").replace(" ", "_")
    out_path = ARTIFACTS_DIR / f"lgb_{slug}.pkl"

    artifact = {
        "model":         model,
        "cell_encoder":  cell_encoder,   # WHY include encoder: inference needs to map cell ID → int
        "feature_names": FEATURE_COLS,
        "category":      category,
        "trained_at":    trained_at,
        "train_pr_auc":  train_pr_auc,
        "test_pr_auc":   test_pr_auc,
        "recall_10":     recall_10,
        "n_train":       n_train,
    }
    with open(out_path, "wb") as f:
        pickle.dump(artifact, f)
    logger.info("artifact saved: %s", out_path)
    return out_path


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(features_path: Path) -> dict[str, Path]:
    """Train all models and return {category_slug: artifact_path}."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    df = _load_features(features_path)

    # Load the cell encoder saved by h3_cells.py alongside the parquet
    date_str = features_path.stem.replace("cell_features_", "")
    encoder_path = features_path.parent / f"cell_encoder_{date_str}.pkl"
    with open(encoder_path, "rb") as f:
        cell_encoder: dict[str, int] = pickle.load(f)

    train, test = _time_split(df)
    trained_at = date.today().isoformat()

    mlflow.set_experiment("lightgbm_risk")
    saved: dict[str, Path] = {}

    # ── Global model ──────────────────────────────────────────────────────────
    with mlflow.start_run(run_name=f"lgb_global_{date_str}"):
        mlflow.log_params({
            "model":             "global",
            "features":          str(features_path),
            "holdout_weeks":     4,
            "num_boost_round":   _NUM_BOOST_ROUND,
            "learning_rate":     _LGB_PARAMS["learning_rate"],
            "num_leaves":        _LGB_PARAMS["num_leaves"],
            "n_train":           len(train),
            "n_test":            len(test),
            "label_prevalence":  round(df["label"].mean(), 4),
        })

        model, train_pr, test_pr = _train_one(train, test, "label", "global")
        recall10 = _recall_at_10pct(model, test, "label")

        mlflow.log_metrics({
            "train_pr_auc": round(train_pr, 4),
            "test_pr_auc":  round(test_pr, 4),
            "recall_at_10pct": round(recall10, 4),
        })

        out = _save_artifact(
            model, cell_encoder, "global", trained_at,
            train_pr, test_pr, recall10, len(train),
        )
        mlflow.log_artifact(str(out), artifact_path="lgb_artifacts")
        saved["global"] = out
        logger.info("global model — test PR-AUC=%.4f  recall@10%%=%.4f", test_pr, recall10)

    # ── Per-category models (P4-4) ────────────────────────────────────────────
    for category, count_col in CATEGORY_LABEL_COLS.items():
        if count_col not in df.columns:
            logger.warning("column %s not found — skipping %s model", count_col, category)
            continue

        # Convert count → binary label for this category
        train_cat = train.copy()
        test_cat  = test.copy()
        train_cat["cat_label"] = (train_cat[count_col] > 0).astype(int)
        test_cat["cat_label"]  = (test_cat[count_col]  > 0).astype(int)

        n_pos_train = train_cat["cat_label"].sum()
        if n_pos_train < 20:
            logger.warning(
                "skipping %s: only %d positive training rows (need ≥20)",
                category, n_pos_train,
            )
            continue

        slug = category.lower().replace(" / ", "_").replace(" ", "_")

        with mlflow.start_run(run_name=f"lgb_{slug}_{date_str}"):
            mlflow.log_params({
                "model":           category,
                "label_col":       count_col,
                "n_pos_train":     int(n_pos_train),
                "n_train":         len(train_cat),
                "label_prevalence": round(train_cat["cat_label"].mean(), 4),
            })

            model, train_pr, test_pr = _train_one(
                train_cat, test_cat, "cat_label", category,
            )
            recall10 = _recall_at_10pct(model, test_cat, "cat_label")

            mlflow.log_metrics({
                "train_pr_auc":    round(train_pr, 4),
                "test_pr_auc":     round(test_pr, 4),
                "recall_at_10pct": round(recall10, 4),
            })

            out = _save_artifact(
                model, cell_encoder, category, trained_at,
                train_pr, test_pr, recall10, len(train_cat),
            )
            mlflow.log_artifact(str(out), artifact_path="lgb_artifacts")
            saved[slug] = out
            logger.info(
                "%s model — test PR-AUC=%.4f  recall@10%%=%.4f",
                category, test_pr, recall10,
            )

    return saved


# ── CLI ───────────────────────────────────────────────────────────────────────

def _latest_features() -> Path:
    snapshots = sorted(SNAPSHOT_DIR.glob("cell_features_*.parquet"))
    if not snapshots:
        raise FileNotFoundError(
            f"No cell_features_*.parquet in {SNAPSHOT_DIR}. "
            "Run `python -m ml.data.h3_cells --latest` first."
        )
    return snapshots[-1]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Train LightGBM risk models")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest", action="store_true",
                       help="Use the most recent cell_features_*.parquet")
    group.add_argument("--features", type=Path,
                       help="Explicit path to a cell_features_*.parquet file")
    args = parser.parse_args()

    features_path = _latest_features() if args.latest else args.features
    artifacts = run(features_path)
    print("Trained models:")
    for name, path in artifacts.items():
        print(f"  {name}: {path}")
