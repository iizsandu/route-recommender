# Route Calculation & Scoring Pipeline

> **Purpose:** Complete technical reference for how the app turns two address
> strings into ranked, risk-banded route recommendations with nearby crime
> incidents.  Read this before touching `routes.py`, `routing.py`, or
> `risk_model.py`.

---

## Table of Contents

1. [High-level overview](#1-high-level-overview)
2. [End-to-end request trace](#2-end-to-end-request-trace)
3. [Step 1 — Geocoding](#3-step-1--geocoding)
4. [Step 2 — Route fetching (ORS)](#4-step-2--route-fetching-ors)
5. [Step 3 — Waypoint sampling](#5-step-3--waypoint-sampling)
6. [Step 4 — Route deduplication](#6-step-4--route-deduplication)
7. [Step 5 — Risk scoring](#7-step-5--risk-scoring)
8. [Step 6 — Risk banding](#8-step-6--risk-banding)
9. [Step 7 — Incident retrieval (Qdrant)](#9-step-7--incident-retrieval-qdrant)
10. [Step 8 — Response construction](#10-step-8--response-construction)
11. [Caching layers](#11-caching-layers)
12. [Configuration reference](#12-configuration-reference)
13. [Key data structures](#13-key-data-structures)
14. [File map](#14-file-map)

---

## 1. High-level overview

```
Browser
  │
  │  POST /routes/recommend
  │  { origin, destination, depart_time }
  ▼
routes.py — orchestrator
  │
  ├─ geocoding.py        Mappls → Nominatim   (address → lat/lng)
  │
  ├─ routing.py          ORS API              (lat/lng pair → up to 3 route geometries)
  │     └─ _sample_waypoints()               (geometry → [lat,lng] every 100m)
  │     └─ _deduplicate_routes()             (drop ORS duplicates)
  │
  ├─ risk_model.py       KDE + time modifier  (waypoints → float score per route)
  │     └─ score_route()
  │          ├─ KDE density per crime category × female-safety weight
  │          ├─ time-of-day multiplier per waypoint
  │          └─ dwell_sec weighting (time spent in each 100m segment)
  │
  ├─ _band()             p33/p66 thresholds   (float score → Low / Medium / High)
  │
  └─ retrieval_service   Qdrant hybrid search (waypoints → nearby crime incidents)

Response: routes sorted safest-first, each with geometry + risk_band + nearby_incidents
```

---

## 2. End-to-end request trace

| # | Location | What happens |
|---|---|---|
| 1 | `RouteForm.jsx` | User submits form. `useRouteRecommend.js` fires `POST /routes/recommend` |
| 2 | `routes.py:recommend()` | FastAPI receives `RouteRequest`. Geocodes origin + dest if they are strings |
| 3 | `geocoding.py:geocode()` | Mappls API → Nominatim fallback → `(lat, lng)` tuple |
| 4 | `routes.py` | Checks `_RESPONSE_CACHE` (5-min TTL). Returns early on hit |
| 5 | `routing.py:get_routes()` | Checks `_cache` (15-min TTL). On miss: calls ORS API |
| 6 | ORS API | Returns 1–3 GeoJSON LineString features (alternative routes) |
| 7 | `routing.py:_sample_waypoints()` | Converts each geometry to a `(lat, lng)` list sampled every 100 m |
| 8 | `routing.py:_deduplicate_routes()` | Removes routes with identical geometry fingerprints |
| 9 | `routes.py` | Loops over deduplicated routes, calls `score_route()` for each |
| 10 | `risk_model.py:score_route()` | Scores every waypoint with weighted KDE density × time modifier × dwell time |
| 11 | `routes.py` | Sorts scored routes ascending (lowest risk first) |
| 12 | `retrieval_service.py` | For each route, fetches nearby historical incidents from Qdrant |
| 13 | `routes.py` | Assembles `RouteOption` objects and returns `RouteResponse` |
| 14 | `MapView.jsx` + `RouteResults.jsx` | Renders routes on map + cards in sidebar |

---

## 3. Step 1 — Geocoding

**File:** `backend/app/services/geocoding.py`  
**Entry point:** `async def geocode(address: str) -> tuple[float, float]`

Only runs when origin/destination is an address string (not raw lat/lng coords).

### Chain

```
geocode(address)
  │
  ├─ _cache.get(key)          24-hour TTL cache — same address never hits the API twice
  │
  ├─ _mappls(address)         Mappls (MapMyIndia) — primary
  │     GET apis.mappls.com/advancedmaps/v2/geocode
  │     params: access_token, address, itemCount=1
  │     parses: copResults[0].lat / copResults[0].lng
  │     best for: Delhi locality names, colonies, informal Indian addresses
  │
  └─ _nominatim(address)      Nominatim (OpenStreetMap) — fallback
        GET nominatim.openstreetmap.org/search
        params: q, format=json, limit=1, countrycodes=in
                viewbox=76.5,29.5,78.0,28.0  (Delhi-NCR bounding box)
                bounded=1  (hard-restrict results to the viewbox)
        WHY bounded: prevents "Saket" resolving to a city in another state
```

**Failure path:** Both APIs return no result → `ValueError` → `HTTPException(422)` with a user-readable message.

---

## 4. Step 2 — Route fetching (ORS)

**File:** `backend/app/services/routing.py`  
**Entry point:** `async def get_routes(origin, dest, profile="driving-car") -> list[dict]`

### ORS API call

```python
POST https://api.openrouteservice.org/v2/directions/driving-car/geojson
Authorization: <ORS_API_KEY>
{
  "coordinates": [[lng_origin, lat_origin], [lng_dest, lat_dest]],
  "alternative_routes": {
    "share_factor": 0.6,   # routes must differ by ≥40% of distance
    "target_count": 3      # ask for up to 3 alternatives
  }
}
```

> **Coordinate order:** ORS uses GeoJSON `[longitude, latitude]` — the opposite of the app's internal `(lat, lng)` convention. The flip happens in the request payload and again in `_sample_waypoints()`.

**Response:** A GeoJSON `FeatureCollection` with 1–3 `LineString` features.
Each feature's `properties.summary` contains `duration` (seconds) and `distance` (metres).

---

## 5. Step 3 — Waypoint sampling

**File:** `backend/app/services/routing.py`  
**Function:** `_sample_waypoints(coordinates) -> list[tuple[float, float]]`

ORS returns a dense polyline (hundreds to thousands of coordinates). The KDE scorer needs evenly-spaced samples, not the raw polyline.

### Algorithm

```
Walk the coordinate list sequentially.
Track accumulated distance (Haversine).
When accumulated ≥ 100m, emit the current point as a waypoint and reset counter.
Output: (lat, lng) tuples at ~100m intervals along the route.
```

**Example:** A 5km route → ~50 waypoints. A 15km route → ~150 waypoints.

**Why 100m intervals?**  
At 100m spacing, a typical Delhi drive produces 50–200 waypoints — enough resolution to detect risky street segments without over-sampling (which would slow KDE evaluation and distort dwell-time weighting).

---

## 6. Step 4 — Route deduplication

**File:** `backend/app/services/routing.py`  
**Functions:** `_route_fingerprint()` → `_deduplicate_routes()`

ORS sometimes returns the same route geometry twice when only one road corridor exists between A and B.

### Fingerprint algorithm

```python
_route_fingerprint(coordinates):
    n = len(coordinates)
    step = max(1, n // 9)
    indices = [0, step, 2*step, ..., n-1]   # up to 10 evenly-spaced indices
    return tuple(
        (round(lng, 3), round(lat, 3))       # 3dp ≈ 111m precision
        for each sampled index
    )
```

Two routes with the same fingerprint share the same road; the second is dropped.

**Why geometry, not distance/duration?**  
Two parallel roads (e.g. NH-48 and the Ring Road) can have identical distance and duration while being genuinely different routes. Sampling the actual coordinates makes the check structurally correct.

**When a duplicate is detected**, a `logger.info("deduplicated ORS routes: N → M unique")` line is emitted — visible in backend logs.

---

## 7. Step 5 — Risk scoring

**File:** `backend/app/services/risk_model.py`  
**Entry point:** `score_route(waypoints, depart_time, route_eta_sec) -> RouteRiskResult`

This is the core ML inference step.

### Full formula

```
route_risk(R, t) =
  Σ_i  [
    base_score(w_i)          ← KDE density weighted by crime category
    × time_modifier(t + eta_i)  ← adjusts for time-of-day danger
    × dwell_sec              ← time spent in each 100m segment
  ]

Where:
  base_score(w_i) = Σ_category  kde_category(w_i.lat, w_i.lng) × female_weight_category
  eta_i           = linspace(0, route_eta_sec, n_waypoints)[i]
  dwell_sec       = route_eta_sec / n_waypoints   (uniform — 100m spacing)
```

### 7a. KDE base score

Each crime category has its own `FixedBandwidthKDE` model loaded from `ml/artifacts/kde_<category>.pkl`.

**Categories and female-safety weights (from `config.py` / `category_mapping.py`):**

| Crime category | Weight | Why |
|---|---|---|
| Sexual Violence | 3.0 | Highest physical safety risk for women |
| Kidnapping | 2.5 | High severity, personal targeting |
| Robbery | 2.0 | Common, direct physical threat |
| Assault | 1.5 | Physical violence |
| Murder | 1.5 | Rare but extreme severity |
| Terrorism / Riot | 1.0 | Area-level danger |
| Theft / Burglary | 0.7 | Lower personal safety risk |
| Drug / Trafficking | 0.5 | Indirect risk |
| Fraud / Cybercrime | 0.0 | Not relevant to physical routing |

```python
# Vectorised: scores all waypoints in one call per category
points = np.vstack([lats, lngs])   # shape (2, n)
base_scores = np.zeros(n)
for macro, kde in model["models"].items():
    weight = model["weights"][macro]
    if weight == 0.0:
        continue
    base_scores += kde(points) * weight   # kde(points) returns shape (n,)
```

### 7b. Time-of-day modifier

Applied **per waypoint** based on when the traveller is expected to reach it (departure time + linear ETA interpolation).

```python
eta_seconds = np.linspace(0, route_eta_sec, n_waypoints)
time_mods = [_time_modifier((depart_time + timedelta(seconds=eta)).hour)
             for eta in eta_seconds]
```

**Time bands:**

| Band | Hours | Multiplier | Rationale |
|---|---|---|---|
| Night | 22:00–05:00 | **2.5×** | Peak danger, lowest foot traffic |
| Evening | 18:00–22:00 | **1.5×** | Post-work, reduced visibility |
| Morning | 05:00–09:00 | **1.0×** | Baseline |
| Daytime | 09:00–18:00 | **0.7×** | Busy streets, lower risk |

> These bands are hand-tuned (not fitted from data). Training data lacks `hour_of_day` for most records.
> See `docs/adr/003-time-multiplier.md` for full rationale.

### 7c. Dwell-time weighting

```python
dwell_sec = route_eta_sec / n_waypoints
total_score = np.sum(per_waypoint_scores * dwell_sec)
```

Time spent in a dangerous area matters more than distance through it.
A slow route through a high-risk zone scores worse than a fast route through the same zone.

### 7d. Optional LightGBM blend (disabled by default)

When `USE_LIGHTGBM=True`:

```python
base_scores = 0.7 * kde_scores + 0.3 * lgb_scores
```

LightGBM predicts P(crime in H3 res-7 cell within 7 days) using spatial density features. Trained separately for Global, Sexual Violence, Robbery, and Assault categories.

**Default is OFF.** KDE is the production model. LightGBM is a Phase 4 enhancement waiting for ≥15,000 labelled records.

### 7e. Output

```python
@dataclass
class RouteRiskResult:
    total_score: float           # used for sorting and banding
    per_waypoint_scores: list    # logged server-side only, never sent to client
    n_waypoints: int
```

---

## 8. Step 6 — Risk banding

**File:** `backend/app/routers/routes.py`  
**Function:** `_band(score, low, high) -> "Low" | "Medium" | "High"`

```python
def _band(score, low, high):
    if score < low:   return "Low"
    if score < high:  return "Medium"
    return "High"
```

**Thresholds (from `.env` / `config.py`):**

| Variable | Default value | Meaning |
|---|---|---|
| `BAND_LOW_THRESHOLD` | `0.0713` | City-wide p33 — bottom third of observed scores |
| `BAND_HIGH_THRESHOLD` | `0.9142` | City-wide p66 — top third of observed scores |

These were calibrated by scoring 400 random points uniformly distributed across Delhi's bounding box (28.40–28.88°N, 76.84–77.55°E) with the current KDE model, then taking the 33rd and 66th percentile of the resulting score distribution.

**Recalibrate after each retrain.** If thresholds go stale, most routes will collapse into one band.

> The raw `total_score` float is **never sent to the client** — only the band label. See `docs/adr/002-three-band-display.md` for the legal and UX rationale.

---

## 9. Step 7 — Incident retrieval (Qdrant)

**File:** `backend/app/services/retrieval_service.py`  
**Function:** `get_route_incidents(waypoints, radius_km, top_k_per_point, max_total)`

Runs **after** all routes are scored. Each scored route independently fetches incidents.

### How it works

1. **Waypoint subsampling** — samples every ~500m (not 100m) to reduce Qdrant query volume by ~4×
2. **Hybrid search per sample point** — dense (bge-small-en-v1.5 embeddings) + BM25 sparse vectors, fused with RRF (k=60)
3. **Geo pre-filter** — bounding box around the sampled point at `radius_km`
4. **Deduplication** — results are deduplicated by URL across all sample points for the route
5. **Graceful degradation** — if Qdrant is unreachable, returns `[]` silently; the route recommendation still works

**Parameters used in `routes.py`:**

```python
retrieval_service.get_route_incidents(
    waypoints=route["waypoints"],
    radius_km=2.0,      # search within 2km of each sampled waypoint
    top_k_per_point=3,  # up to 3 incidents per sample point
    max_total=5,        # cap at 5 unique incidents per route
)
```

### Personalised incidents (separate endpoint)

**File:** `backend/app/routers/search.py`  
**Endpoint:** `POST /routes/incidents/personalised`

Triggered by the questionnaire in the sidebar (not automatically with route recommendation).

- Builds a situation string: `"Woman travelling Alone by Walking arriving at Isolated or poorly lit road"`
- Passes it to `retrieval_service.get_personalised_incidents(situation_text, waypoints, radius_km, max_total)`
- Uses semantic similarity to find incidents matching the user's specific context

---

## 10. Step 8 — Response construction

**File:** `backend/app/schemas/routes.py`

```
RouteResponse
  └── routes: list[RouteOption]
        ├── geometry:          GeoJSON LineString   (sent to MapView for polyline rendering)
        ├── duration_sec:      float                (displayed in sidebar card)
        ├── distance_m:        float                (displayed in sidebar card)
        ├── risk_band:         "Low" | "Medium" | "High"
        └── nearby_incidents:  list[IncidentResult]
              ├── crime_macro, crime_type
              ├── lat, lng      (used by MapView to place dot markers)
              ├── crime_date, summary, url
              ├── location_exact, victim, weapon_used
              └── rrf_score     (debug; not displayed)
```

Routes are **sorted ascending by `total_score`** before being packed into the response. Index 0 is always the safest route — the frontend labels it "🛡 Safest Route", index 1 "⚖ Balanced Route", index 2 "⚡ Fastest Route".

---

## 11. Caching layers

Three independent caches sit at different levels of the pipeline.

| Cache | Location | TTL | Key | What it avoids |
|---|---|---|---|---|
| Geocoding cache | `geocoding.py:_cache` | 24 hours | `address.lower()` | Repeated Mappls/Nominatim API calls for same address |
| ORS route cache | `routing.py:_cache` | 15 minutes | `origin\|dest\|profile` (4dp rounded) | Repeated ORS direction API calls |
| Response cache | `routes.py:_RESPONSE_CACHE` | 5 minutes | `origin-dest-time_band-profile` (3dp) | Full pipeline re-run (scoring + Qdrant) for the same trip |

**Important:** The response cache key uses a **time band** (night / evening / morning / day), not the exact departure time. Two requests 2 minutes apart for the same trip in the same time band share a cache entry.

---

## 12. Configuration reference

All values live in `.env` at repo root, read by `backend/app/config.py` via Pydantic Settings.

| Variable | Default | Effect |
|---|---|---|
| `ORS_API_KEY` | required | Authenticates ORS directions API calls |
| `MAPPLS_API_KEY` | `""` | Empty = skip Mappls, go straight to Nominatim |
| `KDE_ARTIFACTS_DIR` | required | Directory with `kde_*.pkl` per-category model files |
| `BAND_LOW_THRESHOLD` | `0.0713` | Scores below this → Low band |
| `BAND_HIGH_THRESHOLD` | `0.9142` | Scores above this → High band |
| `USE_LIGHTGBM` | `False` | Enable KDE + LightGBM ensemble |
| `KDE_ENSEMBLE_WEIGHT` | `0.7` | KDE weight when LightGBM is on |
| `LGB_ENSEMBLE_WEIGHT` | `0.3` | LightGBM weight when LightGBM is on |
| `QDRANT_URL` | `""` | Qdrant Cloud URL (production) |
| `QDRANT_HOST` | `""` | Qdrant host for local dev (`localhost`) |
| `QDRANT_API_KEY` | `""` | Required when `QDRANT_URL` is set |
| `MODEL_RELOAD_INTERVAL_SECONDS` | `3600` | How often the background task checks MLflow for a newer model |

---

## 13. Key data structures

### Inside `routing.py` (internal, not serialised)

```python
{
  "geometry":     dict,           # GeoJSON LineString from ORS
  "duration_sec": float,          # seconds
  "distance_m":   float,          # metres
  "waypoints":    list[tuple],    # [(lat, lng), ...] every ~100m
}
```

### `RouteRiskResult` (from `risk_model.py`)

```python
@dataclass
class RouteRiskResult:
    total_score:          float        # used for sorting + banding; not sent to client
    per_waypoint_scores:  list[float]  # logged server-side for debugging
    n_waypoints:          int
```

### `RouteOption` (from `schemas/routes.py`, sent to client)

```python
class RouteOption(BaseModel):
    geometry:          dict                      # GeoJSON LineString
    duration_sec:      float
    distance_m:        float
    risk_band:         Literal["Low","Medium","High"]
    nearby_incidents:  list[IncidentResult] = []
```

---

## 14. File map

```
backend/app/
├── routers/
│   ├── routes.py          ← orchestrator: geocode → fetch → score → band → incidents → response
│   └── search.py          ← POST /routes/incidents/personalised
│
├── services/
│   ├── geocoding.py       ← Mappls + Nominatim chain
│   ├── routing.py         ← ORS call, waypoint sampling, deduplication
│   ├── risk_model.py      ← KDE load, score_route(), time modifier, optional LightGBM blend
│   └── retrieval_service.py  ← Qdrant hybrid search wrapper
│
├── schemas/
│   └── routes.py          ← RouteRequest, RouteOption, RouteResponse, IncidentResult
│
└── config.py              ← all env vars and their defaults

ml/
├── kde_model.py           ← FixedBandwidthKDE class (must be importable at serving time)
├── train_kde.py           ← fits KDE models, saves kde_*.pkl to ml/artifacts/
└── artifacts/
    ├── kde_assault.pkl
    ├── kde_robbery.pkl
    ├── kde_sexual_violence.pkl
    └── ...                ← one .pkl per crime category
```
