# backend/app/services/risk_model.py

from __future__ import annotations

import logging
import pickle
import sys
import tempfile
import threading
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import mlflow
import mlflow.artifacts
import numpy as np
from mlflow.tracking import MlflowClient
from prometheus_client import Histogram

# WHY filter: get_latest_versions() is deprecated in MLflow 2.9+ in favour of
# search_model_versions(). The replacement requires a filter-string API change
# that would be a larger refactor; suppress the noise until that migration happens.
warnings.filterwarnings(
    "ignore",
    message=".*get_latest_versions.*",
    category=FutureWarning,
    module="mlflow",
)

# WHY sys.path insert: ml.kde_model lives at repo root (d:\route_recommender_web\ml\).
# When uvicorn is started from backend/, the repo root is not on sys.path, so
# `from ml.kde_model import FixedBandwidthKDE` raises ModuleNotFoundError.
# We resolve the repo root relative to this file's location (always correct
# regardless of CWD) and prepend it once at import time.
_REPO_ROOT = Path(__file__).resolve().parents[3]  # backend/app/services/risk_model.py → repo root
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# WHY import FixedBandwidthKDE: pickle.load() needs the class in scope to
# deserialise kde_*.pkl artifacts. The class lives in ml.kde_model so the
# import path is stable regardless of which script trained the model.
from ml.kde_model import FixedBandwidthKDE  # noqa: F401

logger = logging.getLogger(__name__)

_REGISTRY_MODEL_NAME = "kde_crime_risk"

# Double-buffering state: _MODEL is the live reference; _RELOAD_LOCK prevents
# two concurrent reload threads from racing on the global assignment.
_MODEL: dict | None = None
_LOADED_VERSION: str | None = None   # MLflow model version number last loaded
_RELOAD_LOCK = threading.Lock()

_MODEL_INFERENCE_SECONDS = Histogram(
    "model_inference_seconds",
    "Latency of score_route() in seconds (KDE + optional LightGBM blend)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

# LightGBM ensemble state — populated by load_lightgbm_models() if USE_LIGHTGBM=True.
# Dict keyed by category slug ("global", "sexual_violence", "robbery", "assault").
_LGB_MODELS: dict | None = None
_LGB_LOCK = threading.Lock()

# Female-safety weights for per-category LightGBM blending (mirrors category_mapping.py).
# Only the three categories for which we train LGB models are listed here.
_LGB_CATEGORY_WEIGHTS: dict[str, float] = {
    "sexual_violence": 3.0,
    "robbery":         2.0,
    "assault":         1.5,
}

# Time-of-day multipliers from CLAUDE.md architectural decision #5.
# Stored as (start_hour_inclusive, end_hour_exclusive, multiplier).
_TIME_BANDS: tuple[tuple[int, int, float], ...] = (
    (22, 24, 2.5),  # night (22:00–00:00)
    (0,   5, 2.5),  # night (00:00–05:00)
    (18, 22, 1.5),  # evening
    (5,   9, 1.0),  # morning rush
    (9,  18, 0.7),  # daytime
)


def load_lightgbm_models(artifacts_dir: Path) -> None:
    """Load all lgb_*.pkl artifacts from a directory into _LGB_MODELS.

    Called on startup when USE_LIGHTGBM=True. Silently skips missing category
    files — the global model is sufficient for ensemble scoring.

    Each pkl contains:
        {"model": lgb.Booster, "cell_encoder": {str→int}, "category": str, ...}
    """
    global _LGB_MODELS

    pkl_files = sorted(artifacts_dir.glob("lgb_*.pkl"))
    if not pkl_files:
        raise FileNotFoundError(
            f"No lgb_*.pkl artifacts found in {artifacts_dir}. "
            "Run `python -m ml.train_lightgbm --latest` to generate them."
        )

    models: dict = {}
    for pkl_path in pkl_files:
        with open(pkl_path, "rb") as f:
            artifact = pickle.load(f)
        required = {"model", "cell_encoder", "category", "feature_names"}
        missing = required - set(artifact.keys())
        if missing:
            raise ValueError(
                f"LightGBM artifact {pkl_path.name} is missing keys: {missing}. "
                "Re-run train_lightgbm.py to regenerate."
            )
        slug = artifact["category"].lower().replace(" / ", "_").replace(" ", "_")
        models[slug] = artifact

    with _LGB_LOCK:
        _LGB_MODELS = models

    logger.info(
        "lightgbm models loaded: %s",
        list(models.keys()),
    )


def _score_lgb_batch(
    lats: np.ndarray,
    lngs: np.ndarray,
) -> np.ndarray:
    """Score a batch of (lat, lng) points using the LightGBM ensemble.

    Uses the global model if available, blended with per-category models
    weighted by female-safety weights. Falls back to zeros if _LGB_MODELS
    is not loaded (should not happen when USE_LIGHTGBM=True).

    Returns an array of shape (n,) with values in [0, 1] (probabilities).
    """
    # WHY import h3 here: h3 is an ML dependency, not a backend dependency.
    # Importing at function scope means the backend works without h3 installed
    # when USE_LIGHTGBM=False.
    try:
        import h3 as _h3
    except ImportError:
        logger.error("h3 not installed — LightGBM scoring unavailable")
        return np.zeros(len(lats))

    lgb_models = _LGB_MODELS
    if lgb_models is None:
        return np.zeros(len(lats))

    n = len(lats)

    # Assign each waypoint to its H3 cell (resolution 7)
    cell_ids = [_h3.geo_to_h3(float(lat), float(lng), 7)
                for lat, lng in zip(lats, lngs)]

    def _score_with_model(artifact: dict) -> np.ndarray:
        """Predict probability for each waypoint using one LGB model."""
        cell_encoder: dict[str, int] = artifact["cell_encoder"]
        booster = artifact["model"]
        feature_names: list[str] = artifact["feature_names"]

        # Build feature matrix — one row per waypoint
        rows = []
        for cell_id in cell_ids:
            enc = cell_encoder.get(cell_id, -1)  # -1 for unseen cells (OOD)
            row = {f: 0 for f in feature_names}
            row["h3_cell_enc"] = enc
            # WHY leave temporal features as 0: at inference time we don't
            # know the exact travel week. The spatial features (density cols)
            # are unknown too without a pre-built panel. The cell ID alone
            # provides the primary signal for the routing use case.
            rows.append(row)

        import pandas as pd
        X = pd.DataFrame(rows, columns=feature_names)
        return booster.predict(X)

    if "global" in lgb_models:
        scores = _score_with_model(lgb_models["global"])
    else:
        scores = np.zeros(n)

    # Blend in per-category models weighted by female-safety weights
    cat_total_weight = sum(_LGB_CATEGORY_WEIGHTS.values())
    for slug, weight in _LGB_CATEGORY_WEIGHTS.items():
        if slug in lgb_models:
            cat_scores = _score_with_model(lgb_models[slug])
            # WHY additive not multiplicative: scores are probabilities (0-1).
            # Additive blend allows each category to independently elevate risk.
            scores = scores + (weight / cat_total_weight) * cat_scores

    return scores


def _load_artifacts_from_dir(artifacts_dir: Path) -> dict:
    """Load all kde_*.pkl files from a directory and assemble the model dict.

    Each .pkl contains one category's artifact:
        {"kde": <FixedBandwidthKDE>, "weight": float, "category": str, ...}

    Assembles them into the internal model dict:
        {"models": {category: kde, ...}, "weights": {category: float, ...},
         "fit_at": str, "n_categories": int}

    Raises FileNotFoundError if no kde_*.pkl files are found.
    Raises ValueError if any artifact is missing required keys.
    """
    pkl_files = sorted(artifacts_dir.glob("kde_*.pkl"))
    if not pkl_files:
        raise FileNotFoundError(
            f"No kde_*.pkl artifacts found in {artifacts_dir}. "
            "Run `python -m ml.train_kde --latest` to generate them."
        )

    models:  dict = {}
    weights: dict = {}
    fit_at:  str  = "unknown"

    for pkl_path in pkl_files:
        with open(pkl_path, "rb") as f:
            artifact = pickle.load(f)

        # Validate required keys — fail fast on a corrupt artifact.
        required = {"kde", "weight", "category", "trained_at"}
        missing  = required - set(artifact.keys())
        if missing:
            raise ValueError(
                f"Artifact {pkl_path.name} is missing keys: {missing}. "
                "Re-run train_kde.py to regenerate."
            )

        cat = artifact["category"]
        models[cat]  = artifact["kde"]
        weights[cat] = artifact["weight"]
        # Use the most recent trained_at across all artifacts as fit_at.
        if artifact["trained_at"] > fit_at:
            fit_at = artifact["trained_at"]

    return {
        "models"      : models,
        "weights"     : weights,
        "fit_at"      : fit_at,
        "n_categories": len(models),
    }


def load_model(artifacts_dir: Path) -> None:
    """Load per-category KDE artifacts from a directory into the global model.

    Args:
        artifacts_dir: Directory containing kde_*.pkl files produced by
                       train_kde.py (typically ml/artifacts/).
    """
    global _MODEL
    _MODEL = _load_artifacts_from_dir(artifacts_dir)
    logger.info(
        "risk model loaded: %d categories, fit_at=%s",
        _MODEL["n_categories"],
        _MODEL["fit_at"],
    )


def reload_from_registry() -> bool:
    """Pull the latest Production model from the MLflow registry and swap
    it in atomically (double-buffering pattern). Safe to call from a thread.

    MLflow stores per-category kde_*.pkl files as a directory artifact.
    Downloads the whole directory, loads all artifacts, then swaps _MODEL.

    Returns True if the model was reloaded, False if already up to date or
    if the reload failed (existing model is kept in both failure cases).
    """
    global _MODEL, _LOADED_VERSION

    try:
        client = MlflowClient()
        versions = client.get_latest_versions(
            _REGISTRY_MODEL_NAME, stages=["Production"]
        )
    except Exception as exc:
        # WHY split log levels: "not found" means promote_model.py hasn't been
        # run yet — expected in dev, not an operational failure. Any other
        # exception (network error, auth failure) is a real WARNING.
        if "not found" in str(exc).lower():
            logger.debug(
                "MLflow registry model %r not yet registered — skipping reload",
                _REGISTRY_MODEL_NAME,
            )
        else:
            logger.warning("registry check failed, keeping current model: %s", exc)
        return False

    if not versions:
        logger.warning("no Production model in registry '%s'", _REGISTRY_MODEL_NAME)
        return False

    latest = versions[0]

    # WHY compare version strings: avoids a full artifact download on every
    # hourly tick. Only download when the registry has a newer version.
    if latest.version == _LOADED_VERSION:
        logger.debug("model v%s already loaded — no reload needed", _LOADED_VERSION)
        return False

    logger.info(
        "new Production model detected: v%s (current: %s) — reloading",
        latest.version, _LOADED_VERSION,
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # WHY models:/ URI: resolves the Production-stage artifact directory
            # without needing the run_id. Works with both local SQLite and
            # remote MLflow tracking servers.
            local_dir = mlflow.artifacts.download_artifacts(
                artifact_uri=f"models:/{_REGISTRY_MODEL_NAME}/Production",
                dst_path=tmpdir,
            )
            new_model = _load_artifacts_from_dir(Path(local_dir))

        # WHY lock around the assignment: prevents a second concurrent reload
        # thread (e.g. a manual /admin/reload call) from interleaving with this
        # one and leaving _MODEL and _LOADED_VERSION out of sync.
        with _RELOAD_LOCK:
            _MODEL = new_model
            _LOADED_VERSION = latest.version

        logger.info(
            "hot-reload complete: model v%s, %d categories, fit_at=%s",
            latest.version, new_model["n_categories"], new_model["fit_at"],
        )
        return True

    except Exception as exc:
        logger.error("hot-reload failed, keeping current model: %s", exc)
        return False


def _require_model() -> dict:
    # WHY separate helper: every public function calls this instead of
    # checking _MODEL directly — one place to update the error message.
    if _MODEL is None:
        raise RuntimeError("risk model not loaded — call load_model() first")
    return _MODEL


def _time_modifier(hour: int) -> float:
    for start, end, multiplier in _TIME_BANDS:
        if start <= hour < end:
            return multiplier
    # WHY fallback 1.0: should never reach here given bands cover 0–24,
    # but a safe default beats a silent KeyError.
    return 1.0


def score_points_batch(
    lats: np.ndarray,
    lngs: np.ndarray,
    hour: int,
    kde_weight: float = 0.7,
    lgb_weight: float = 0.3,
) -> np.ndarray:
    """Score a batch of (lat, lng) points. Returns shape (n,) score array.

    When _LGB_MODELS is loaded (USE_LIGHTGBM=True in config), blends KDE and
    LightGBM scores. LGB probabilities (0-1) are small relative to raw KDE
    densities (0-35), so blending acts as a mild additive correction without
    requiring threshold recalibration.

    When _LGB_MODELS is None (default), returns pure KDE score unchanged.
    """
    model = _require_model()
    time_mod = _time_modifier(hour)

    # Stack into shape (2, n) — the format gaussian_kde.evaluate() expects.
    points = np.vstack([lats, lngs])   # shape (2, n)
    kde_scores = np.zeros(points.shape[1])

    for macro, kde in model["models"].items():
        weight = model["weights"].get(macro, 0.0)
        if weight == 0.0:
            continue
        # WHY kde(points) not kde.evaluate(points): __call__ is an alias
        # for evaluate() — same result, shorter syntax.
        kde_scores += kde(points) * weight

    if _LGB_MODELS is not None:
        lgb_scores = _score_lgb_batch(lats, lngs)
        # WHY not normalise: LGB probabilities (0-1) vs KDE densities (0-35)
        # means LGB contributes ~1-3% of total score. This preserves KDE
        # banding thresholds while letting LGB provide marginal refinement.
        blended = kde_weight * kde_scores + lgb_weight * lgb_scores
        return blended * time_mod

    return kde_scores * time_mod


@dataclass
class RouteRiskResult:
    total_score: float
    per_waypoint_scores: list[float]
    n_waypoints: int


def score_route(
    waypoints: list[tuple[float, float]],  # list of (lat, lng)
    depart_time: datetime,
    route_eta_sec: float,
    kde_weight: float = 0.7,
    lgb_weight: float = 0.3,
) -> RouteRiskResult:
    with _MODEL_INFERENCE_SECONDS.time():
        return _score_route_impl(waypoints, depart_time, route_eta_sec, kde_weight, lgb_weight)


def _score_route_impl(
    waypoints: list[tuple[float, float]],
    depart_time: datetime,
    route_eta_sec: float,
    kde_weight: float,
    lgb_weight: float,
) -> RouteRiskResult:
    if not waypoints:
        return RouteRiskResult(total_score=0.0, per_waypoint_scores=[], n_waypoints=0)

    model = _require_model()
    n = len(waypoints)

    lats = np.array([w[0] for w in waypoints])
    lngs = np.array([w[1] for w in waypoints])
    points = np.vstack([lats, lngs])          # shape (2, n)

    # Base KDE scores — vectorised across all waypoints, no time modifier yet.
    base_scores = np.zeros(n)
    for macro, kde in model["models"].items():
        weight = model["weights"].get(macro, 0.0)
        if weight == 0.0:
            continue
        base_scores += kde(points) * weight

    # Blend in LightGBM if loaded (time modifier applied after blending).
    if _LGB_MODELS is not None:
        lgb_scores = _score_lgb_batch(lats, lngs)
        base_scores = kde_weight * base_scores + lgb_weight * lgb_scores

    # Per-waypoint time modifier — eta interpolated linearly to each waypoint.
    eta_seconds = np.linspace(0, route_eta_sec, n)
    time_mods = np.array([
        _time_modifier((depart_time + timedelta(seconds=float(e))).hour)
        for e in eta_seconds
    ])

    per_waypoint = base_scores * time_mods

    # WHY divide by n: dwell_sec is the time spent per segment. Uniform
    # spacing (100m samples) means each waypoint represents equal time.
    dwell_sec = route_eta_sec / n
    total_score = float(np.sum(per_waypoint * dwell_sec))

    return RouteRiskResult(
        total_score=total_score,
        per_waypoint_scores=per_waypoint.tolist(),
        n_waypoints=n,
    )


