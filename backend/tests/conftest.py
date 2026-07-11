# backend/tests/conftest.py

import os
from unittest.mock import MagicMock

import numpy as np
import pytest

# WHY module-level setdefault: routing.py and config.py call Settings() at import
# time. If the required env vars are absent, pydantic raises ValidationError before
# any test can run. Setting them here (before any app import) avoids that.
os.environ.setdefault(
    "COSMOS_CONNECTION_STRING",
    "AccountEndpoint=https://dummy.documents.azure.com:443/;AccountKey=ZHVtbXk=;",
)
os.environ.setdefault("KDE_ARTIFACTS_DIR", "/tmp/fake-kde-artifacts")
os.environ.setdefault("BAND_LOW_THRESHOLD", "0.07")
os.environ.setdefault("BAND_HIGH_THRESHOLD", "0.91")


@pytest.fixture()
def fake_kde():
    """KDE mock that returns np.ones(n) for any batch of n points."""
    kde = MagicMock()
    # gaussian_kde is called as kde(points) where points.shape = (2, n)
    kde.side_effect = lambda points: np.ones(points.shape[1])
    return kde


@pytest.fixture()
def fake_model_dict(fake_kde):
    """Minimal model dict that satisfies _require_model() and score_points_batch()."""
    return {
        "models":       {"Sexual Violence": fake_kde},
        "weights":      {"Sexual Violence": 3.0},
        "fit_at":       "2026-05-16T00:00:00",
        "n_categories": 1,
    }
