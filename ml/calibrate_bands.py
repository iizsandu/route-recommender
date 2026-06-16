"""
Recalibrate Low / Medium / High banding thresholds against the current model.

WHY this exists: score_route() returns a TIME-ACCUMULATED total
(route_eta_sec x mean per-waypoint KDE score), not a per-point density. The
BAND_LOW/HIGH thresholds in .env must therefore be the p33/p66 of real ROUTE
total_scores — calibrating on per-point scores (the old method) mis-scales them
by 2-3 orders of magnitude and bands every route "High".

What it does: samples N random Delhi point-pairs from the GraphHopper graph
(guaranteed routable car-edge vertices), routes each through the running GH,
scores each route with the backend's own score_route(), then prints the p33/p66
of the resulting total_scores. Paste those into .env as BAND_LOW_THRESHOLD /
BAND_HIGH_THRESHOLD.

Usage:
    python -m ml.calibrate_bands                       # 150 routes, balanced, noon
    python -m ml.calibrate_bands --n 250 --profile safest
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

# Repo root + backend on sys.path so we can import the real scoring code.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.services import risk_model  # noqa: E402

_GH_EDGES = _REPO_ROOT / "ml" / "artifacts" / "gh_edges.csv"
_ARTIFACTS = _REPO_ROOT / "ml" / "artifacts"

# Delhi NCT bbox (matches routing.py) — keep sampled pairs inside the graph.
_LAT_MIN, _LAT_MAX = 28.40, 28.88
_LNG_MIN, _LNG_MAX = 76.84, 77.35


def _load_vertices(path: Path, cap: int = 8000) -> list[tuple[float, float]]:
    """Return up to `cap` (lat, lng) vertices sampled from car-edge geometries."""
    verts: list[tuple[float, float]] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            m = re.search(r"\(([\d.]+) ([\d.]+)", row["geom_wkt"])
            if not m:
                continue
            lng, lat = float(m.group(1)), float(m.group(2))
            if _LAT_MIN <= lat <= _LAT_MAX and _LNG_MIN <= lng <= _LNG_MAX:
                verts.append((lat, lng))
    random.shuffle(verts)
    return verts[:cap]


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat = math.radians(b[0] - a[0])
    dlng = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150, help="number of routes to score")
    ap.add_argument("--profile", default="balanced")
    ap.add_argument("--gh", default="http://localhost:8989")
    ap.add_argument("--hour", type=int, default=12, help="depart hour for the time modifier")
    ap.add_argument("--min-km", type=float, default=2.0, help="min straight-line pair distance")
    ap.add_argument("--max-km", type=float, default=12.0, help="max straight-line pair distance")
    args = ap.parse_args()

    # Load the SAME model the backend serves (KDE + optional LightGBM ensemble).
    risk_model.load_model(_ARTIFACTS)
    try:
        risk_model.load_lightgbm_models(_ARTIFACTS)
        print("[calibrate] LightGBM ensemble loaded (matches backend USE_LIGHTGBM=True)")
    except Exception as exc:
        print(f"[calibrate] LightGBM not loaded ({exc}); scoring pure KDE")

    verts = _load_vertices(_GH_EDGES)
    print(f"[calibrate] {len(verts)} candidate vertices; routing {args.n} pairs via GH ...")

    depart = datetime(2026, 1, 1, args.hour, 0, 0, tzinfo=timezone.utc)
    scores: list[float] = []
    attempts = 0
    with httpx.Client(timeout=20.0) as client:
        while len(scores) < args.n and attempts < args.n * 8:
            attempts += 1
            o = random.choice(verts)
            d = random.choice(verts)
            km = _haversine_km(o, d)
            if not (args.min_km <= km <= args.max_km):
                continue
            payload = {
                "points": [[o[1], o[0]], [d[1], d[0]]],  # GH wants [lng, lat]
                "profile": args.profile,
                "points_encoded": False,
                "instructions": False,
            }
            try:
                r = client.post(f"{args.gh}/route", json=payload)
                if r.status_code != 200:
                    continue
                path = r.json()["paths"][0]
            except Exception:
                continue
            coords = path["points"]["coordinates"]      # [[lng, lat], ...]
            waypoints = [(c[1], c[0]) for c in coords]   # -> (lat, lng)
            eta_sec = path["time"] / 1000.0
            result = risk_model.score_route(
                waypoints=waypoints, depart_time=depart, route_eta_sec=eta_sec
            )
            scores.append(result.total_score)

    if len(scores) < 10:
        print(f"[calibrate] ERROR: only {len(scores)} routes scored — is GH running?")
        sys.exit(1)

    arr = np.array(scores)
    p33, p66 = np.percentile(arr, [33, 66])
    print(f"\n[calibrate] scored {len(arr)} routes (profile={args.profile}, hour={args.hour})")
    print(f"  min={arr.min():.4f}  p33={p33:.4f}  median={np.median(arr):.4f}  "
          f"p66={p66:.4f}  max={arr.max():.4f}")
    print("\n=== Paste into .env ===")
    print(f"BAND_LOW_THRESHOLD={p33:.4f}")
    print(f"BAND_HIGH_THRESHOLD={p66:.4f}")


if __name__ == "__main__":
    main()
