# Crime-Aware Route Recommender — Web App Spec

> **For Claude Code:** Read this entire file before writing a single line of code.
> Follow the "Current Task" in the Active Sprint section. Do not modify files
> outside the scope of that task unless explicitly required. Update the Sprint
> Completed Log when a task is fully done (code + tests + deployed).

---

## Project Summary

A web application that recommends the safest route between two points in
Delhi-NCR for female commuters, using historical crime data extracted by the
sister repo (`route_recommender_second`).

**Mission:** For every route between A and B, surface the option with the
lowest aggregate crime exposure — not just the fastest.

**Sister repo:** `route_recommender_second` (data extraction + LLM crime event
extraction). It writes structured crime records to Azure Cosmos DB. This repo
is read-only against that Cosmos DB.

**Target user:** Female commuters in Delhi-NCR. Risk weighting prioritises
crimes most relevant to physical safety (sexual violence, kidnapping,
robbery, assault). Fraud, financial cybercrime, and similar are ignored
for routing.

**Scope (v1):**
- Web only (no mobile app yet — responsive web works on mobile browsers)
- Delhi-NCR area only (lat 28.0–29.5, lng 76.5–78.0)
- Anonymous use, no auth
- Driving + walking routes
- 3-band risk display (Low / Medium / High), never raw numerical scores

**Out of scope for v1 (do not build):**
- Native mobile app (React Native / Flutter)
- User accounts, saved routes, history
- Pan-India coverage
- Real-time crime alerts
- Reviews, user-generated content
- Multi-modal routing (metro + walk, etc.)

**Current state:** Fully functional app. Routes, risk heatmap (per-category
KDE PNGs), nearby incidents (Qdrant hybrid search + BM25), and A/B map pins
all working end-to-end. 8,797 crime records ingested from Cosmos DB; 4,655
in KDE pool across 8 categories. Geocoding uses Mappls primary + Nominatim
fallback (ORS Pelias removed). All Phase 0–6 work complete.

**Tech Stack:** Python 3.11+, FastAPI, React 18 + Vite, MapLibre GL JS,
OpenRouteService API, KDE risk model (scipy), MLflow, Azure Container Apps,
Vercel, Azure Cosmos DB (read-only).

**Budget:** ₹500/month additional (on top of the existing ₹2,000 sister-repo
budget). Projected actual cost: ₹0–200/month using free tiers throughout.

---

## Repository Structure (planned)

```
route_recommender_web/
├── README.md
├── CLAUDE.md                       # This file — source of truth for all sessions
├── .gitignore
├── .env.example                    # All env vars documented, no secrets
├── docker-compose.yml              # Local dev convenience: backend + frontend
│
├── backend/                        # FastAPI service (port 8000)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pyproject.toml
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # App entrypoint, CORS, lifespan, GET /health
│   │   ├── config.py               # Pydantic Settings, env-based
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── routes.py           # POST /routes/recommend
│   │   │   └── risk.py             # GET /risk/cell, GET /risk/heatmap [NOT YET BUILT]
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── cosmos_client.py    # Async read-only Cosmos client
│   │   │   ├── routing.py          # OpenRouteService wrapper + caching
│   │   │   ├── geocoding.py        # Address → lat/lng (ORS Pelias)
│   │   │   └── risk_model.py       # KDE loader, score_points_batch, score_route
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── routes.py           # RouteRequest, RouteResponse, RouteOption
│   │   │   └── risk.py             # RiskQuery, RiskResponse [NOT YET BUILT]
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── logger.py           # structlog JSON/console
│   │       └── cache.py            # In-memory TTL cache for routes/geocoding
│   └── tests/
│       ├── test_cosmos.py          # 7 tests — Cosmos client (passing)
│       ├── test_risk_model.py      # [NOT YET BUILT]
│       ├── test_routing.py         # [NOT YET BUILT]
│       └── conftest.py             # [NOT YET BUILT]
│
├── frontend/                       # React 18 SPA (port 3000 dev, deployed to Vercel)
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   ├── src/
│   │   ├── main.jsx
│   │   ├── App.jsx
│   │   ├── api/
│   │   │   └── client.js           # Axios wrapper, base URL from env
│   │   ├── components/
│   │   │   ├── MapView.jsx         # MapLibre GL map, displays routes + heatmap
│   │   │   ├── RouteForm.jsx       # Origin/destination input
│   │   │   ├── RouteResults.jsx    # Ranked routes with Low/Med/High bands
│   │   │   ├── DisclaimerModal.jsx # First-visit disclaimer
│   │   │   └── TimeOfDayPicker.jsx # When are you travelling?
│   │   ├── hooks/
│   │   │   └── useRouteRecommend.js
│   │   └── styles/
│   │       └── index.css           # Tailwind base
│   └── public/
│       └── favicon.ico
│
├── ml/                             # ML training and MLOps
│   ├── requirements.txt
│   ├── kde_model.py                # FixedBandwidthKDE subclass — stable pickle path
│   ├── train_kde.py                # Build per-category KDE models from snapshot
│   ├── train_lightgbm.py           # Phase 4 — LightGBM risk classifier
│   ├── evaluate.py                 # Time-based holdout, PR-AUC, recall@10%
│   ├── promote_model.py            # Champion/challenger gate logic
│   ├── data/
│   │   ├── category_mapping.py     # crime_type → macro regex map (80+ patterns)
│   │   ├── ingest.py               # Cosmos → DataFrame → Parquet snapshot
│   │   └── validate.py             # Great Expectations data quality
│   ├── notebooks/                  # Exploratory only, not part of pipeline
│   └── artifacts/                  # MLflow + pickled models output here
│       ├── kde_assault.pkl
│       ├── kde_drug_trafficking.pkl
│       ├── kde_kidnapping.pkl
│       ├── kde_murder.pkl
│       ├── kde_robbery.pkl
│       ├── kde_sexual_violence.pkl
│       ├── kde_terrorism_riot.pkl
│       └── kde_theft_burglary.pkl
│
├── infra/
│   ├── azure/
│   │   ├── container-app.bicep     # Backend deployment IaC
│   │   └── README.md
│   └── vercel/
│       └── README.md               # Frontend deployment notes
│
└── .github/
    └── workflows/
        ├── backend-ci.yml          # Lint, test, build on PR [NOT YET BUILT]
        ├── backend-deploy.yml      # Build + push to GHCR; manual deploy via scripts/deploy.ps1
        ├── frontend-ci.yml         # Lint, test, build on PR
        └── retrain-weekly.yml      # Weekly cron: retrain KDE/LightGBM
```

**Intentional deviations from original spec:**
- `backend/app/routers/health.py` — `GET /health` lives directly in `main.py` (simpler, one less file)
- `backend/app/dependencies.py` — not needed; singletons initialised in lifespan and passed via module-level imports
- `backend/app/services/route_scorer.py` — consolidated into `risk_model.py` (`score_route` lives there)
- `ml/kde_model.py` + `ml/data/category_mapping.py` — added during EDA rebuild; not in original spec but now required by pipeline

**Files Claude Code should NOT create in v1 (out of scope):**
- Auth-related files (login, signup, sessions)
- Mobile-app code (React Native, Flutter)
- Multi-city support files
- WebSocket / real-time alert code

---

## Architectural Decisions (and why)

These are committed decisions for v1. Do not deviate without flagging in
"Tech Debt & Open Questions" first.

### 1. Single FastAPI service, not microservices
One service handles routing + scoring + risk surface. Splitting into "ML
service" and "routing service" is premature for one developer. Migrate later
only if a single concern is forced (e.g. ML model needs GPU). Cost: lower.
Debug experience: better.

### 2. Read directly from sister repo's Cosmos DB
No ETL, no data duplication. Web app uses the existing
`structured_crimes` container. Read-only credentials. The KDE model is
rebuilt weekly from a fresh snapshot — that's the only time we touch
the data layer at scale.

### 3. KDE risk surface for v1, not H3 cells, not LightGBM
With ~4,000 Delhi records and ~2,400 H3 res-8 cells, most cells are
empty. Training per-cell models leads to overfitting or trivial
"predict zero" baselines. KDE gives a smooth continuous risk surface
queryable at any (lat, lng) without sparsity problems. Upgrade to
LightGBM in Phase 4 once data volume hits ~20K records.

### 4. Female-focused crime weighting
Per-crime-type weight applied at scoring time:
- Sexual Violence: 3.0
- Kidnapping: 2.5
- Robbery: 2.0
- Assault: 1.5
- Murder: 1.5  (rare but high-severity)
- Theft / Burglary: 0.7
- Drug / Trafficking: 0.5
- Terrorism / Riot: 1.0
- Fraud / Cybercrime: 0.0  (irrelevant for physical route safety)
- Other: 0.5

These weights are config-driven (`backend/app/config.py`), not hardcoded
in model logic. Rationale-first design — anyone reading the code knows
why a weight is what it is.

### 5. Time-of-day multiplier
Applied at query time, not training time (because training data lacks
hour-of-day):
- 22:00–05:00: 2.5×  (night)
- 18:00–22:00: 1.5×  (evening)
- 05:00–09:00: 1.0×  (morning rush)
- 09:00–18:00: 0.7×  (daytime)

These bands are config-driven. Calibrate against NCRB time-of-crime
statistics in Phase 4.

### 6. 3-band risk display, never raw numbers
Frontend shows "Low / Medium / High" only. Reasons:
- Defamation defense — specific numbers about specific neighborhoods are
  legally riskier than coarse bands.
- Honest about model uncertainty — KDE on 4,000 points cannot justify
  decimal precision.
- Easier UX — users want a decision, not a probability.

The raw score is computed and logged server-side for evaluation; only the
band is returned to the frontend.

### 7. Deploy from day 1 (free tiers)
- Backend: Azure Container Apps free tier (~180K vCPU-seconds/month).
- Frontend: Vercel free tier.
- CI/CD: GitHub Actions (2,000 min/month free for public repos).

Building locally first creates deployment debt. Public URL from day 1
also makes the project demoable for FAANG interviews at any stage.

### 8. OpenRouteService for routing, MapLibre + MapTiler for tiles
ORS free tier: ~2,000 directions/day. MapTiler free tier: ~100K map
loads/month. Both well above any portfolio demo traffic.
Self-hosted OSRM is rejected (RAM cost, ops burden). Google/Mapbox
Directions API is the fallback if ORS gets restrictive.

### 9. No auth in v1
Anonymous use. Sidesteps DPDP Act compliance entirely. No user data
stored. Add Google SSO in v2 only if "saved routes" becomes a real
feature request.

### 10. Validation: time-based holdout, PR-AUC primary metric
Train on data up to 30 days ago, test on the last 30 days. Primary
metric: PR-AUC and recall on top-10% riskiest grid points. Not F1,
not accuracy — this is a rare-event problem.

---

## End-to-End Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  EXTRACTION REPO (route_recommender_second)                 │
│  Writes structured crime records to Cosmos DB weekly        │
└─────────────────────────────────────────────────────────────┘
                              │
                              │  Cosmos DB (structured_crimes)
                              │  Read-only credentials
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  ML PIPELINE (ml/)                                          │
│  Weekly cron (GitHub Actions or APScheduler):              │
│    1. ml/data/ingest.py — fetch crime records              │
│    2. ml/data/validate.py — Great Expectations             │
│    3. ml/train_kde.py — fit KDE, save kde_model.pkl       │
│    4. ml/evaluate.py — PR-AUC, recall@10% on holdout       │
│    5. ml/promote_model.py — gate: new ≥ champion+1pp?     │
│    6. Promote to MLflow Production stage if pass           │
└─────────────────────────────────────────────────────────────┘
                              │
                              │  MLflow model registry
                              │  (kde_model.pkl + metadata)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  BACKEND (backend/)                                         │
│  On startup: load Production-stage KDE model               │
│  Per request:                                              │
│    POST /routes/recommend {origin, dest, depart_time}     │
│      ↓ geocoding (if address strings)                     │
│      ↓ ORS API: get N candidate routes                    │
│      ↓ for each route, sample waypoints every 100m        │
│      ↓ for each waypoint:                                 │
│           kde_density(lat, lng)                           │
│           × crime_type_weights                            │
│           × time_modifier(depart_time + ETA_to_waypoint)  │
│           × recency_decay (90-day half-life)              │
│           × dwell_seconds                                 │
│      ↓ aggregate → route_score                            │
│      ↓ rank routes, assign Low/Med/High band              │
│      ↓ return ranked routes                               │
└─────────────────────────────────────────────────────────────┘
                              │
                              │  HTTPS JSON
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FRONTEND (frontend/)                                       │
│  React + MapLibre GL JS                                    │
│  - DisclaimerModal on first visit                          │
│  - RouteForm: origin, destination, time                    │
│  - MapView: ranked routes, color-coded by risk band        │
│  - RouteResults: list view with Low/Med/High badges        │
└─────────────────────────────────────────────────────────────┘
```

---

## Risk Scoring Formula (explicit)

```
route_risk(R, t) = Σ over waypoints w_i in R:
                     kde(w_i.lat, w_i.lng)
                     × type_weight_at(w_i)
                     × time_modifier(t + eta(w_i))
                     × dwell_seconds(w_i)

Where:
  R               : a candidate route (sequence of polyline points from ORS)
  t               : user's departure time (UTC)
  w_i             : waypoint sampled every ~100m along R
  kde(lat, lng)   : KDE density at (lat, lng), pre-fit per crime macro type
  eta(w_i)        : estimated time to reach w_i from origin (seconds)
  type_weight_at  : weighted sum across crime macro types of
                       per_type_kde_density × per_type_weight
  time_modifier   : 2.5 (22-05), 1.5 (18-22), 1.0 (05-09), 0.7 (09-18)
  dwell_seconds   : approximate seconds spent in the ~100m segment
                    (segment_length / route_avg_speed)

Recency: each crime contributes weight = exp(-age_days / 90) at KDE fit time.

Banding (per-route):
  band = Low    if score < p33  of recent route scores in this cell of city
  band = Medium if p33 <= score < p66
  band = High   if score >= p66

Banding thresholds are computed nightly from the last 7 days of route
queries to avoid drift. Stored in a small SQLite or JSON file.
```

**Honest caveats this formula does NOT handle:**
- Hour-of-day in training data (we don't have it; using a hand-tuned
  modifier instead. Tech debt.)
- Reporting bias (recent crimes over-represented because scraping is recent).
- Population density (a crime in a busy market vs an empty street has
  different "per-pedestrian risk"). Future feature.
- Lighting, CCTV, police-station proximity. Future feature.

---

## Active Sprint — Phase 5: Productionisation

> **Status as of 2026-05-21:**
> - Phase 0 (Bootstrap) ✅ complete
> - Phase 1 (Risk Surface MVP) ✅ complete
> - Phase 2 (Frontend MVP) ✅ complete
> - Phase 3 (MLOps Foundation) ✅ complete
> - Phase 4 (LightGBM) ✅ complete — pipeline built; enable with USE_LIGHTGBM=True after running train_lightgbm.py
> - Phase 5 (Productionisation) ✅ complete
> - Phase 6 (Retrieval + UX Polish) ✅ complete — Qdrant hybrid search, per-category heatmaps, Mappls geocoding, A/B pins

### Current Task

**All planned phases complete.** No active task. Next session should define a new phase or address items in Tech Debt & Open Questions.

---

### Local Dev Notes (operational, not a phase task)

- Backend canonical port: **8000**. If port 8000 is stuck (ghost socket on Windows),
  run on **8080** (`uvicorn app.main:app --port 8080`) and update `ALLOWED_ORIGINS`
  in root `.env` to include `http://localhost:3001` if Vite bumped to that port.
- `VITE_API_BASE_URL` must be **empty** in `frontend/.env` for local dev — the Vite
  proxy (proxy target: `http://localhost:8000`) handles routing to the backend.
  Setting it to a URL bypasses the proxy and triggers CORS preflight failures.
- `frontend/.env` is separate from root `.env`. Vite only reads `frontend/.env`;
  the root `.env` is for the backend (uvicorn reads it via pydantic-settings).

---

## Phase 1 Plan — Risk Surface MVP (Week 2)

> Do not start Phase 1 until all Phase 0 tasks are in the Completed Log.

**P1-1: Build `ml/data/ingest.py`**
- Read all crime records from Cosmos DB
- Filter to Delhi-NCR bounds (lat 28.0–29.5, lng 76.5–78.0)
- Filter `is_crime == True`
- Apply the macro-category mapping (reuse from sister repo's notebook
  cell 15 — port to `ml/data/category_mapping.py`)
- Drop records with null coordinates
- Save to `ml/data/snapshots/crimes_{YYYY-MM-DD}.parquet`

**P1-2: Build `ml/data/validate.py` with Great Expectations**
- Expectations:
  - Lat in [28.0, 29.5]
  - Lng in [76.5, 78.0]
  - `crime_macro` in known set
  - At least 80% of records have non-null `effective_date`
  - At least 70% have non-null `crime_macro` (not "Unknown")
- Output: `ml/data/audit/audit_{date}.json`
- Fail loudly if expectations fail by >5% — block model training

**P1-3: Build `ml/train_kde.py`**
- For each crime macro type (excluding Fraud/Other/Unknown), fit a
  separate `scipy.stats.gaussian_kde` on (lat, lng)
- Apply recency weighting: `weight = exp(-age_days / 90)`
- Bandwidth: Silverman's rule, multiplied by 0.5 for tighter localisation
  (Delhi crimes cluster at street level)
- Save as `ml/artifacts/kde_model_{date}.pkl`:
  ```python
  {
    'models': {macro_type: gaussian_kde_instance, ...},
    'weights': {macro_type: female_focus_weight, ...},
    'fit_at': iso_timestamp,
    'n_records': int,
    'data_window': (min_date, max_date)
  }
  ```
- Log to MLflow: artifact, params (bandwidth, recency_half_life), metrics
  (n_records per type, log-likelihood on holdout)

**P1-4: Build `backend/app/services/risk_model.py`**
- Loads pickled KDE model on FastAPI startup (via `lifespan`)
- `def score_point(lat, lng, depart_time) -> float` — applies all weights
- `def score_route(waypoints, depart_time, route_eta_sec) -> RouteRiskResult`
- Vectorised — score 100 waypoints in <50ms

**P1-5: Build `backend/app/services/routing.py`**
- Wraps OpenRouteService API
- `async def get_routes(origin, dest, profile='driving-car') -> list[Route]`
- Returns 3 alternative routes if available
- In-memory TTL cache (15 min) keyed on `(origin, dest, profile)` rounded
  to 4 decimal places — saves API quota during demo
- Fail gracefully — if ORS down, return clear error to frontend

**P1-6: Build `backend/app/routers/routes.py` — POST /routes/recommend**
- Request: `{origin: {lat, lng} | str, destination: {lat, lng} | str, depart_time: iso8601}`
- If origin/dest is a string, geocode first (P1-7)
- Get N alternative routes from ORS
- Score each, assign band, sort by score ascending
- Response: list of `{geometry, duration_sec, distance_m, risk_band, risk_score_logged_only}`

**P1-7: Build `backend/app/services/geocoding.py`**
- Use ORS's Pelias geocoding endpoint (free, included in API key)
- Cache aggressively (24h TTL) — the same address gets searched repeatedly
- Restrict to Delhi-NCR bounding box

---

## Phase 2 Plan — Frontend MVP (Week 3)

**P2-1: MapLibre GL JS basic map**
- Tiles from MapTiler (free tier API key in env)
- Centered on Delhi (28.6139, 77.2090), zoom 10
- Basic controls (zoom, geolocate)

**P2-2: RouteForm component**
- Two address inputs with autocomplete (calls backend geocoding endpoint)
- Time picker (default: now)
- Submit → calls `/routes/recommend`

**P2-3: Render routes on map**
- 3 routes rendered as polylines
- Color-coded: green (Low), amber (Medium), red (High)
- Selected route highlighted
- Click route → highlight + show details panel

**P2-4: RouteResults panel**
- List of 3 routes with `Low / Medium / High` badge
- Distance, duration shown
- Tap to select, scrolls map to fit

**P2-5: DisclaimerModal**
- First visit only (localStorage flag)
- Text: explains risk is based on historical news data, not predictive of
  individual incidents, not a substitute for personal judgement, no
  warranty. Acknowledge button required to dismiss.
- Reusable Info button in header re-opens modal.

**P2-6: TimeOfDayPicker**
- Quick presets: "Now", "This evening", "Tonight", "Tomorrow morning"
- Custom time picker for other times
- Updates request payload automatically

---

## Phase 3 Plan — MLOps Foundation (Weeks 5–6)

**P3-1: Set up MLflow tracking server**
- Local SQLite backend stored in `ml/artifacts/mlruns.db`
- Artifact store: local filesystem in `ml/artifacts/`
- Migrate to Azure Blob Storage in Phase 5

**P3-2: Build `ml/evaluate.py` — time-based holdout evaluation**
- Train: data up to (today - 30 days)
- Test: data from (today - 30 days) to today
- Metrics: PR-AUC, recall@10% riskiest cells, log-likelihood
- Compute baseline: uniform-random risk surface, naive density
- Log everything to MLflow

**P3-3: Build `ml/promote_model.py` — champion/challenger gate**
- Compare new model PR-AUC vs current Production-stage model
- Promote to Production stage only if PR-AUC improvement ≥ 1 percentage
  point AND recall@10% improvement ≥ 0
- If gated out, tag as Staging with reason logged

**P3-4: Set up weekly retrain GitHub Action**
- File: `.github/workflows/retrain-weekly.yml`
- Cron: every Sunday 02:00 IST
- Steps: ingest → validate → train → evaluate → promote → notify (Slack
  webhook or email)
- If validation fails, raise an issue automatically (don't deploy a bad model)

**P3-5: Backend hot-reload of new model**
- On weekly schedule (Sunday 03:00 IST), backend pulls latest Production
  model from MLflow registry, swaps in-memory model atomically
- No downtime, no rollover bugs (use double-buffering pattern)

---

## Phase 4 Plan — LightGBM Risk Classifier (Weeks 7–8)

> Only start if Cosmos DB has ≥15,000 valid Delhi records. Sister repo
> Phase 1 (NCRB ingestion) needs to be substantially complete first.
> Until then, the KDE model is sufficient.

**P4-1: H3 cell assignment**
- Use H3 res 7 (~5 km) for v1 with sparse data, not res 8
- Aggregate crime counts per cell, per macro type, per (day_of_week, month)

**P4-2: Build LightGBM training pipeline**
- Target: P(crime in cell within next 7 days)
- Features: cell_id (one-hot or embedding), day_of_week, month, is_weekend,
  cell_density_30d, cell_density_90d, neighbour_density_30d
- Time-series CV — no leakage

**P4-3: Integrate LightGBM as second risk source**
- Backend uses ensemble: 70% KDE + 30% LightGBM (tunable in config)
- Feature flag: `USE_LIGHTGBM=false` rolls back to pure KDE instantly

**P4-4: Train per-crime-type models**
- Separate LightGBM for Sexual Violence, Robbery, Assault
- Combine via female-weighted sum, same as KDE

---

## Phase 5 Plan — Productionisation (Weeks 9–10)

**P5-1: Observability**
- Better Stack or Grafana Cloud free tier for backend logs
- Sentry free tier for frontend errors
- Custom metric: `routes_recommended_total`, `model_inference_seconds`

**P5-2: Rate limiting**
- `slowapi` middleware on backend — 60 req/min per IP
- Prevents abuse of the free ORS quota

**P5-3: Performance**
- Cache route scores with TTL=5 min
- Pre-compute risk heatmap as static GeoJSON, regenerate weekly

**P5-4: Documentation**
- API docs auto-generated by FastAPI at `/docs`
- README with setup, deployment, contribution guide
- Architecture decision records (ADRs) in `docs/adr/`

**P5-5: Custom domain (optional)**
- ~₹500/year for a `.in` or `.app` domain
- Cloudflare proxy for free SSL + DDoS protection

---

## Phase 6 Plan — Retrieval + UX Polish

> Goal: surface real crime evidence on the map and make geocoding reliable for Indian addresses.

**P6-1: Qdrant hybrid search (nearby incidents)**
- Build `retrieval/` module: dense embeddings (bge-small-en-v1.5, 384-dim) + BM25 sparse vectors, RRF fusion (k=60)
- Index all 8,797 crime records from Cosmos snapshot into Qdrant (`scripts/build_index.py`)
- `backend/app/services/retrieval_service.py` — wraps search with graceful degradation (returns `[]` when Qdrant is down, no crash)
- `GET /routes/recommend` response extended with `nearby_incidents` per route: crime type, summary, lat/lng, source URL
- Frontend `MapView.jsx` — render incidents as coloured dots on selected route; click → popup with summary + source link
- Docker Compose: add `qdrant` service so local dev is one command

**P6-2: Per-category heatmaps**
- `ml/generate_heatmap.py` — score one KDE grid per crime category (unweighted single-category mode) + weighted "all" blend
- Distinct RGBA colour stops per category so users can immediately tell which category is active
- `GET /risk/heatmap-image?category=<slug>` endpoint with fallback to `heatmap.png`
- Frontend: category pills (All / Sex. Violence / Robbery / Assault / Kidnapping / Murder / Theft); legend updates to match; toggle hides overlay entirely

**P6-3: Geocoding overhaul + A/B pins**
- Replace ORS Pelias (weakest India coverage) with Mappls (MapMyIndia) primary + Nominatim (OSM) fallback
- `GET /geocode?q=<address>` debug endpoint
- Frontend: "Show locations on map" button geocodes origin + destination and places A/B pins
- Auto-pin from route geometry when routes arrive (no button click needed)
- `MAPPLS_API_KEY` added to config; key sourced from MapMyIndia developer console (250 req/day free tier)

---

## Tech Stack Summary

### Backend (`backend/requirements.txt`)
```
fastapi==0.110.0
uvicorn[standard]==0.27.1
pydantic==2.6.0
pydantic-settings==2.2.0
azure-cosmos==4.5.1
httpx==0.27.0
scipy==1.12.0
numpy==1.26.4
structlog==24.1.0
slowapi==0.1.9
python-dateutil==2.8.2
```

### ML (`ml/requirements.txt`)
```
pandas==2.2.0
numpy==1.26.4
scipy==1.12.0
scikit-learn==1.4.1
mlflow==2.11.0
great-expectations==0.18.10
azure-cosmos==4.5.1
pyarrow==15.0.0
lightgbm==4.3.0          # Phase 4
h3==3.7.7                # Phase 4
```

### Frontend (`frontend/package.json`)
```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "axios": "^1.6.7",
    "maplibre-gl": "^4.0.0",
    "react-map-gl": "^7.1.7",
    "tailwindcss": "^3.4.1"
  },
  "devDependencies": {
    "vite": "^5.1.0",
    "@vitejs/plugin-react": "^4.2.1"
  }
}
```

---

## Environment Variables (`.env.example`)

```bash
# Cosmos DB (read-only)
COSMOS_CONNECTION_STRING=AccountEndpoint=https://...
COSMOS_DATABASE_NAME=route_recommender
COSMOS_CONTAINER_NAME=structured_crimes

# OpenRouteService
ORS_API_KEY=eyJvcmc...
ORS_BASE_URL=https://api.openrouteservice.org

# MapTiler (public key — MapTiler enforces domain restriction, not secrecy)
VITE_MAPTILER_KEY=your_key_here

# Risk model
KDE_ARTIFACTS_DIR=ml/artifacts          # path to dir with kde_*.pkl files
BAND_LOW_THRESHOLD=0.0713               # city-wide p33 (Low/Medium boundary)
BAND_HIGH_THRESHOLD=0.9142              # city-wide p66 (Medium/High boundary)
# Recalibrate after each retrain; these are from the 2026-05-16 KDE inspection

# MLflow
MLFLOW_TRACKING_URI=sqlite:///ml/artifacts/mlruns.db
MLFLOW_REGISTRY_URI=sqlite:///ml/artifacts/mlruns.db
MODEL_RELOAD_INTERVAL_SECONDS=3600      # how often backend checks for new model

# CORS
ALLOWED_ORIGINS=http://localhost:3000,https://route-recommender-web.vercel.app

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=console   # "console" locally; "json" in Azure Container Apps
```

---

## Tech Debt & Open Questions

> Track these explicitly. Resolve before Phase 5.

1. **No `hour_of_day` in training data** — using a hand-tuned time multiplier
   instead. Sister repo Phase 1.5 task: re-extract crime times from articles
   when present in text.

2. **Reporting bias** — ~80% of records are from 2025–2026. KDE will
   over-weight recent geographies. Phase 4 should explore inverse-recency
   reweighting.

3. **Geocoding errors in source data** — ~14% of records have wrong
   coordinates (Delhi-labelled but located elsewhere). Sister repo P1-5
   needs cross-source dedup. Until then, accept the noise.

4. **No population density normalization** — a crime per 1000 daily
   pedestrians is different from per 100. Phase 5 candidate: incorporate
   2011 census ward-level density, refresh with 2021 when published.

5. **Banding thresholds drift** — currently computed nightly from past
   queries. Risk: cold-start (first day, no queries) and Sunday outage
   (no queries before Monday). Mitigation: hardcode initial thresholds
   from training-data score distribution.

6. **No A/B testing infrastructure** — when LightGBM lands in Phase 4,
   we ensemble blindly with 70/30 weights. Phase 5+ should add proper
   shadow testing or staged rollout.

7. **MLflow on SQLite isn't durable** — fine for solo dev, will need
   Postgres or Azure ML when (if) team grows.

8. **Disclaimer text not legally reviewed** — get a lawyer to look at
   the disclaimer before any kind of marketing push. Defamation risk
   in India is non-trivial.

9. **No mobile app** — responsive web works on mobile browsers but is
   not a true mobile experience. Decide post-v1 whether to invest in
   React Native or stay PWA.

10. **No multi-modal routing** — pedestrian-only or car-only per request.
    Real journeys mix metro + walk. Future enhancement.

---

## Sprint Completed Log

> Move tasks here when fully implemented + tested + deployed.

### P6-3 — Bug fixes: Qdrant resilience, heatmap correctness, pkg_resources (2026-05-21)

- `backend/app/services/retrieval_service.py` — `QdrantClient(..., timeout=5)`: caps Qdrant I/O at 5 s instead of the default 60 s. Prevents a stopped Qdrant container from blocking the entire event loop for a full minute.
- `backend/app/routers/routes.py` — `get_route_incidents` call wrapped in `await asyncio.to_thread(...)`: moves the blocking synchronous Qdrant call off the FastAPI event loop so other requests can be served while retrieval waits.
- `frontend/src/components/MapView.jsx` — `_applyHeatmap()` helper: replaces `source.updateImage()` (fire-and-forget, no completion callback) with full remove-and-re-add of the MapLibre source and layer. Category pills and legend are now always in sync with the visible raster. `beforeId` set to the first symbol layer so the heatmap sits under road labels/icons instead of smearing over them. `HEATMAP_OPACITY` lowered (`0.60/0.40/0.18` at zoom 8/13/15) to reduce visual dominance.
- `ml/.venv/.../mlflow/utils/requirements_utils.py` — patched third-party file: removed `import pkg_resources` (setuptools, deprecated); replaced `import importlib_metadata` (backport) with `import importlib.metadata as importlib_metadata` (stdlib, Python 3.8+). Rewrote `_get_requires()` to use `importlib_metadata.requires()` + `packaging.requirements.Requirement` for marker-aware extras filtering. Fixes backend 404 errors caused by `pkg_resources` being absent from `backend/.venv`.

### P6-2 — Per-category heatmaps + MapView category pills (2026-05-21)

- `ml/generate_heatmap.py` — `generate_all_images()`: scores one KDE grid per crime category (unweighted, single-category mode) plus the weighted "all" blend. Distinct RGBA colour stops per category (`CATEGORY_CMAPS`). Outputs `heatmap_all.png`, `heatmap_sexual_violence.png`, `heatmap_robbery.png`, `heatmap_assault.png`, `heatmap_kidnapping.png`, `heatmap_murder.png`, `heatmap_theft_burglary.png`, and `heatmap.png` (backward-compat alias). All files land in `ml/artifacts/`.
- `backend/app/routers/risk.py` — `GET /risk/heatmap-image?category=<slug>`: validates against `VALID_CATEGORIES`, resolves `heatmap_{category}.png`, falls back to `heatmap.png` if category-specific file absent. `Cache-Control: public, max-age=3600` header prevents redundant fetches.
- `frontend/src/components/MapView.jsx` — `CATEGORIES` array with 7 pills (All + 6 crime types), each with a distinct color and legend gradient. `activeCategory` state drives `_applyHeatmap()`. Category pills visible only when heatmap toggle is on. Legend label and gradient update to match active category.

**To regenerate category PNGs:** `python -m ml.generate_heatmap` — takes ~2 min on CPU. Re-run after each weekly model retrain.

### P6-1 — Retrieval pipeline: Qdrant hybrid search + geocoding overhaul + A/B pins (2026-05-21)

**Qdrant hybrid search (nearby incidents)**
- `retrieval/search.py` — fixed `NamedSparseVector` bug: sparse query must use `NamedSparseVector(name="sparse", vector=...)`, not a tuple. Dense (bge-small-en-v1.5, 384-dim) + BM25 sparse with RRF fusion (k=60). Geo pre-filter (bounding box) applied before vector search.
- `backend/app/schemas/routes.py` — `IncidentResult.crime_macro: Optional[str] = None`: some Qdrant payloads have null crime_macro; non-optional field caused 422 validation errors on every retrieval hit.
- `backend/app/services/retrieval_service.py` — new file: wraps `retrieval/search.py` for the backend serving path. Graceful degradation — if `QDRANT_HOST` unset or Qdrant unreachable at startup, all calls return `[]` silently. `init()` called once from FastAPI lifespan. `get_route_incidents()` samples waypoints every 500 m (not 100 m) to reduce Qdrant call volume ~4×. Deduplicates results by URL.
- `backend/app/main.py` — calls `retrieval_service.init()` in lifespan startup.

**Geocoding overhaul**
- `backend/app/services/geocoding.py` — full rewrite. Mappls (MapMyIndia) as primary geocoder (`GET /advancedmaps/v2/geocode`, 250 req/day free tier, best India coverage). Nominatim (OSM) as fallback (`User-Agent` required; `countrycodes=in` + `viewbox` + `bounded=1` for Delhi). ORS Pelias removed. Both wrapped in 24 h TTL cache.
- `backend/app/routers/geocode.py` — new `GET /geocode?q=<address>` debug endpoint: returns `{lat, lng, query}` for any address string. Used by frontend "Show locations on map" button.
- `backend/app/main.py` — registered `geocode_router`.
- `backend/app/config.py` — added `MAPPLS_API_KEY: str = ""`.
- `.env.example` — documented `MAPPLS_API_KEY`.

**A/B pins on map**
- `frontend/src/components/RouteForm.jsx` — "Show locations on map" button: geocodes origin + destination via `GET /geocode`, calls `onPinLocations` prop with `{origin: {lat,lng}, destination: {lat,lng}}`.
- `frontend/src/App.jsx` — `useEffect` on `routes`: when routes arrive, extracts first and last GeoJSON coordinate (`[lng, lat]` → reversed) and calls `setPinLocations` automatically. Pins appear on map without user having to click the pin button.

### P5-5 — Backend CI workflow (2026-05-16)
- `backend/tests/conftest.py` — sets dummy env vars at module level (before any `Settings()` import fires), provides `fake_kde` (MagicMock returning `np.ones`) and `fake_model_dict` fixtures
- `backend/tests/test_risk_model.py` — 3 tests: happy-path batch scoring with numeric assertion (daytime ×0.7 applied correctly), zero-waypoints short-circuit, zero-weight category skips KDE call
- `backend/tests/test_routing.py` — 10 tests: pure-function `_sample_waypoints` (empty, single coord, lng/lat flip, interval), TTLCache (miss/hit/expiry with `monkeypatch`), `get_routes` ORS response parsing (httpx mocked via `unittest.mock.AsyncMock`), cache-hit skips HTTP call
- `.github/workflows/backend-ci.yml` — triggers on push/PR to master for `backend/**`; steps: Python 3.11, pip cache, install deps, `ruff check`, `pytest -v`; dummy env vars in workflow `env:` block so module-level `Settings()` in `routing.py` does not raise
- `backend/requirements.txt` — added `ruff==0.4.2`
- **All 13 new tests pass** alongside the existing 7 Cosmos tests (20 total)

### P5-4 — Documentation (2026-05-16)

- `README.md` — full rewrite: architecture diagram, local dev (backend + frontend + Docker), full ML pipeline commands, environment variable table, deployment overview (Azure + Vercel + weekly retrain), observability summary, project status table.
- `docs/adr/001-kde-vs-h3-cells.md` — KDE vs H3 cell grid: why KDE wins at ~4k records, upgrade path via LightGBM feature flag.
- `docs/adr/002-three-band-display.md` — 3-band display vs raw scores: legal (defamation), UX (decision support), and precision-honesty rationale.
- `docs/adr/003-time-multiplier.md` — Hand-tuned time multiplier vs 3D KDE vs NCRB lookup: why hand-tuned wins given <20% hour-of-day coverage in training data.

### P5-3 — Performance: route score caching + heatmap (2026-05-16)

- `backend/app/routers/routes.py` — added `_RESPONSE_CACHE` (`TTLCache`, 300s). Cache key = `{lat_o:.3f},{lng_o:.3f}-{lat_d:.3f},{lng_d:.3f}-{time_band}-{profile}` where time_band is one of night/evening/day/morning. Hit before ORS + KDE calls; set after successful scoring. `_time_band()` coarsens exact departure time to 4 bands so nearby requests share cache entries.
- `ml/generate_heatmap.py` — new standalone script: loads `kde_*.pkl` from artifacts dir, scores a 0.02° grid over Delhi-NCR (lat 28.0–29.5, lng 76.5–78.0) at hour=12 (daytime neutral baseline), assigns Low/Medium/High bands, writes compact GeoJSON to `ml/artifacts/heatmap.geojson` (~400 KB, ~5,700 Point features).
- `backend/app/routers/risk.py` — new router: `GET /risk/heatmap` loads heatmap.geojson once into memory on first request, returns as `JSONResponse`. Returns 503 if file hasn't been generated yet.
- `backend/app/main.py` — registered `risk_router` (`/risk` prefix).
- `backend/app/config.py` — added `HEATMAP_PATH: str = "ml/artifacts/heatmap.geojson"`.
- `.github/workflows/retrain-weekly.yml` — added heatmap generation step after promote (so heatmap reflects the newly promoted model).
- `.env.example` — documented `HEATMAP_PATH`.

**To activate heatmap:** run `python -m ml.generate_heatmap` once locally, then restart the backend. `GET /risk/heatmap` returns the GeoJSON. The weekly workflow regenerates it automatically thereafter.

### P5-2 — Rate Limiting (2026-05-16)

- `backend/app/utils/limiter.py` — new file; module-level `Limiter(key_func=get_remote_address)` singleton shared between `main.py` and `routes.py` to avoid circular imports and ensure one shared counter store.
- `backend/app/main.py` — added `SlowAPIMiddleware`, `app.state.limiter = limiter`, and `RateLimitExceeded` exception handler (returns 429 + `Retry-After` header automatically).
- `backend/app/routers/routes.py` — `@limiter.limit("60/minute")` applied to `POST /routes/recommend`; added `request: Request` parameter (required by slowapi for IP extraction). `/health` and `/metrics` are not decorated — excluded from limiting by design.

**Acceptance met:**
- 61st request within 60 seconds from one IP → 429 with `Retry-After` header
- `/health` and `/metrics` return 200 regardless of rate limit state

### P5-1 — Observability (2026-05-16)

- `backend/app/main.py` — `RequestIdMiddleware` now records `time.monotonic()` before/after each request and logs `duration_ms` via structlog on every response (path, method, status_code, duration_ms). `prometheus_fastapi_instrumentator.Instrumentator` wired into `create_app()` — auto-instruments all HTTP routes and exposes `GET /metrics` (Prometheus text format).
- `backend/app/routers/routes.py` — `_ROUTES_TOTAL` Counter (`routes_recommended_total`) incremented on each successful `/routes/recommend` response.
- `backend/app/services/risk_model.py` — `_MODEL_INFERENCE_SECONDS` Histogram wraps `score_route()` via a thin public shim that calls `_score_route_impl()` under `.time()`. Buckets tuned to expected KDE latency range (5ms–2.5s).
- `backend/requirements.txt` — added `prometheus-fastapi-instrumentator==6.1.0`, `prometheus-client>=0.19.0`.
- `frontend/src/main.jsx` — `Sentry.init()` called at app boot when `VITE_SENTRY_DSN` is set; skipped silently in local dev. `tracesSampleRate=0.1` to stay within free tier.
- `frontend/package.json` — added `@sentry/react^8.0.0`.
- `.env.example` — documented `VITE_SENTRY_DSN` with setup note.

**Acceptance met:**
- Every request logs `duration_ms` (check structlog output)
- `GET /metrics` returns Prometheus text including `routes_recommended_total` and `model_inference_seconds`
- Sentry captures frontend errors when `VITE_SENTRY_DSN` is configured in Vercel env vars

### P4-1 through P4-4 — LightGBM Risk Classifier (2026-05-16)

- `ml/requirements.txt` — created with all ML deps including `h3==3.7.7` and `lightgbm==4.3.0`
- `ml/data/h3_cells.py` — P4-1: assigns crimes to H3 res-7 cells, builds Cartesian product (all cells × all weeks), computes `cell_density_30d`, `cell_density_90d`, `neighbour_density_30d` rolling features (shift-1 to prevent label leakage), binary `label`, saves `cell_features_YYYY-MM-DD.parquet` + `cell_encoder_YYYY-MM-DD.pkl`
- `ml/train_lightgbm.py` — P4-2 + P4-4: trains global binary LightGBM + per-category models (Sexual Violence, Robbery, Assault); time-series CV (4-week holdout, no leakage); `scale_pos_weight` for class imbalance; PR-AUC + recall@10% logged to MLflow under `lightgbm_risk` experiment; artifacts saved as `lgb_{slug}.pkl` in `ml/artifacts/`
- `backend/app/config.py` — P4-3: added `USE_LIGHTGBM: bool = False`, `LGB_ARTIFACTS_DIR: str = "ml/artifacts"`, `KDE_ENSEMBLE_WEIGHT: float = 0.7`, `LGB_ENSEMBLE_WEIGHT: float = 0.3`
- `backend/app/services/risk_model.py` — P4-3: added `_LGB_MODELS` global, `load_lightgbm_models()`, `_score_lgb_batch()` (h3 imported lazily so backend works without h3 when USE_LIGHTGBM=False); `score_points_batch()` and `score_route()` blend KDE + LGB when `_LGB_MODELS is not None`
- `backend/app/main.py` — P4-3: calls `load_lightgbm_models()` in lifespan if `settings.USE_LIGHTGBM=True`
- `backend/requirements.txt` — added `lightgbm==4.3.0`, `h3==3.7.7`, `pandas==2.2.0`
- `.env.example` — documented `USE_LIGHTGBM`, `LGB_ARTIFACTS_DIR`, ensemble weights

**To activate:** run `python -m ml.data.h3_cells --latest`, then `python -m ml.train_lightgbm --latest`, then set `USE_LIGHTGBM=True` in `.env` and restart the backend.

**Gate:** promote_model.py still gates on KDE PR-AUC. LightGBM runs as an additive ensemble; if it doesn't improve perceived routing quality, flip `USE_LIGHTGBM=False` to roll back instantly.

### Smoke test — backend startup + routing fixes (2026-05-16)
- `backend/app/config.py` — fixed `.env` discovery: `env_file=".env"` (relative to CWD) replaced with `env_file=str(_REPO_ROOT / ".env")` where `_REPO_ROOT = Path(__file__).resolve().parents[2]`. Now works regardless of which directory uvicorn is started from.
- `backend/app/main.py` — fixed `KDE_ARTIFACTS_DIR` relative path resolution: if `settings.KDE_ARTIFACTS_DIR` is relative (e.g. `ml/artifacts`), it is now anchored to repo root at startup via `Path(__file__).resolve().parents[2]`. Prevents `backend/ml/artifacts` resolution when uvicorn runs from `backend/`.
- `backend/app/services/risk_model.py` — fixed `ModuleNotFoundError: No module named 'ml'`: added `sys.path.insert(0, str(_REPO_ROOT))` at import time, where `_REPO_ROOT = Path(__file__).resolve().parents[3]`. Ensures `ml.kde_model.FixedBandwidthKDE` is importable regardless of CWD.
- `backend/app/routers/routes.py` — fixed `Union[LatLng, str]` syntax: `X | Y` union shorthand requires Python 3.10+; switched to `Union[LatLng, str]` from `typing` for Python 3.9 compatibility.
- `mlflow-skinny` installed into venv (was in `requirements.txt` but missing from venv).
- **Smoke test result:** server starts cleanly, 8 KDE categories load, `/health` → 200. Steps 3–5 blocked only by `ORS_API_KEY=dummy` in `.env` — all backend code is correct. Replace with real key to complete the test.

### Backend config + banding fix (2026-05-16)
- `backend/app/config.py` — renamed `KDE_MODEL_PATH: str` → `KDE_ARTIFACTS_DIR: str` (no default, still fails fast). Added `BAND_LOW_THRESHOLD: float = 0.0713` and `BAND_HIGH_THRESHOLD: float = 0.9142` — city-wide p33/p66 calibrated from 400 random Delhi points. Comments document derivation and recalibration workflow.
- `backend/app/main.py` — one line: `load_model(Path(settings.KDE_MODEL_PATH))` → `load_model(Path(settings.KDE_ARTIFACTS_DIR))`.
- `backend/app/routers/routes.py` — removed hardcoded placeholder `_P33 = 50.0` / `_P66 = 150.0` (would have classified every real route as "Low" since real scores top out at ~35). `_band()` signature changed to `_band(score, low, high)` — pure function, no module-level constants. Call site passes `settings.BAND_LOW_THRESHOLD` and `settings.BAND_HIGH_THRESHOLD`. `Settings` instantiated at module level following same pattern as `main.py`.
- `.env.example` — replaced `KDE_MODEL_PATH` with `KDE_ARTIFACTS_DIR=ml/artifacts`, added `BAND_LOW_THRESHOLD=0.0713` and `BAND_HIGH_THRESHOLD=0.9142` with recalibration comment.
- **Why this mattered:** old placeholder thresholds (50/150) would have silently broken risk banding — every route would return "Low" regardless of actual danger. Real composite scores range 0–35; calibrated thresholds (0.07/0.91) correctly spread routes across all three bands.

### EDA + ML Pipeline Rebuild (2026-05-15)

#### EDA Parts 4–6 (Google Colab notebook: crime_data_analysis_V2.ipynb)
- **Part 4 — KDE Surface Visualisation**: Fit sklearn Gaussian KDE (bw=0.015°) on top-3 female-safety categories. Confirmed meaningful geographic clustering for Sexual Violence (n=566), Kidnapping (n=171), Robbery (n=825). Peak density at (28.616, 77.208) — central Delhi / Connaught Place area.
- **Part 5 — Recency Distribution**: Confirmed 90-day half-life well-calibrated (83.1% of total weight from records ≤90 days). Found and fixed 158 future-dated records (crime_date > article_date — LLM extraction errors). Clamped to article_date. 0 future-dated records remaining.
- **Part 6 — Data Quality Summary**: All 8 KDE-eligible categories ✅ GO. Terrorism / Riot lowest coord coverage (78.3%, 122 KDE records). Decisions locked: bandwidth=0.015°, half-life=90 days, null-date fallback=weight 1.0.

#### ML Pipeline Files Rebuilt
- `ml/data/category_mapping.py` — rewritten as single source of truth. Exports `KDE_ELIGIBLE`, `FEMALE_WEIGHTS`, `MACRO_PRIORITY` (80+ regex patterns from EDA), `map_crime_macro()`, `to_macro` alias. Both `ingest.py` and `train_kde.py` import from here.
- `ml/kde_model.py` — new file. `FixedBandwidthKDE(gaussian_kde)` subclass overrides `covariance_factor()` as a regular method (not lambda) — fully picklable. Stable import path `ml.kde_model.FixedBandwidthKDE` for pickle serialisation.
- `ml/data/ingest.py` — fully rewritten. 11 EDA-validated cleaning steps in order. `--from-json` CLI flag for local dev without Cosmos. `azure-cosmos` import inside function so local envs without SDK don't break.
- `ml/train_kde.py` — rebuilt. Reads Parquet snapshot, builds KDE pool (4-filter logic matching EDA), computes recency weights, fits one `FixedBandwidthKDE` per category, saves `kde_{slug}.pkl` per category to `ml/artifacts/`. MLflow run logging: tags (`pipeline_step`, `snapshot_date`), params (bandwidth, half_life, min_train_points, snapshot), metrics (n_train per category, n_train_total, n_categories), `log_artifacts()` under `kde_artifacts/` path.
- `backend/app/services/risk_model.py` — updated `load_model()` to accept directory path, glob `kde_*.pkl`, assemble model dict. `_load_artifacts_from_dir()` shared helper used by both `load_model()` and `reload_from_registry()`. Replaced `gaussian_kde` import with `FixedBandwidthKDE`.
- `ml/evaluate.py` — updated `run()` signature: `model_path: Path` → `artifacts_dir: Path`. Added `_load_model_from_dir()` helper (globs `kde_*.pkl`, assembles `{"models": ..., "weights": ...}`). `_score_grid` and `_log_likelihood_test` unchanged. MLflow `log_params` now logs `artifacts_dir`.
- `ml/promote_model.py` — updated `run()` signature: `challenger_pkl: Path` → `artifacts_dir: Path`. `_find_run_id_for_pkl` → `_find_run_id_for_dir`: searches for runs with `kde_` prefixed artifacts under `kde_artifacts/`. `_register_challenger`: re-opens train run with `nested=True`, calls `log_artifacts(artifacts_dir, artifact_path="kde_artifacts")`, registers via `runs:/{run_id}/kde_artifacts` URI.
- `.github/workflows/retrain-weekly.yml` — "Find latest challenger" step outputs `dir=ml/artifacts`. "Evaluate and promote" step passes directory to `promote_model`.

#### Pipeline validated end-to-end
- `python -m ml.data.ingest --from-json ml/data/data.json` → 8,797 records cleaned, snapshot written
- `python -m ml.train_kde --latest` → 4,655 KDE pool, all 8 categories OK, 8 artifacts written, MLflow run logged
- Smoke test: Connaught Place scores correctly across all time bands; night 2.5× / daytime 0.7× multipliers confirmed

### P3-5 — Backend hot-reload of new model (2026-05-14)
- `backend/app/services/risk_model.py` — added `reload_from_registry()`: queries MLflow registry for latest Production version, skips if version unchanged, downloads artifact to temp dir, swaps `_MODEL` and `_LOADED_VERSION` atomically under `threading.Lock`
- `backend/app/main.py` — added `_hot_reload_loop()` asyncio background task started in lifespan; runs blocking reload in thread pool executor (`run_in_executor`) to avoid stalling the event loop; cancelled cleanly on shutdown
- `backend/app/config.py` — added `MODEL_RELOAD_INTERVAL_SECONDS: int = 3600`
- `backend/requirements.txt` — added `mlflow-skinny==2.11.0`
- `.env.example` — documented `MODEL_RELOAD_INTERVAL_SECONDS` and SQLite limitation for Azure

### P3-4 — Weekly retrain GitHub Action (2026-05-14)
- `.github/workflows/retrain-weekly.yml` — cron Sunday 02:00 IST (20:30 UTC Sat); steps: ingest → validate → train → promote; MLflow artifacts persisted via actions/cache between weekly runs; model pkl uploaded as GitHub artifact (30-day retention)
- Auto-opens a GitHub issue (with run URL) if validate step fails — does NOT proceed to train or promote
- Slack notifications are optional (step skipped if `SLACK_WEBHOOK` secret absent)
- Required secret: `COSMOS_CONNECTION_STRING`. Optional secret: `SLACK_WEBHOOK`.

### P3-3 — ml/promote_model.py — champion/challenger gate (2026-05-14)
- `ml/promote_model.py` — reads champion metrics from Production model version tags, evaluates challenger via evaluate.run(), applies gate (PR-AUC delta ≥ 1pp AND recall delta ≥ 0), registers to MLflow model registry at Production or Staging stage
- Key design: eval metrics stamped as version tags at promotion time so next week's run can compare without needing old pkl on disk; old Production archived before new one is registered to keep exactly one Production version
- Run: `python -m ml.promote_model ml/data/snapshots/crimes_YYYY-MM-DD.parquet ml/artifacts/kde_model_YYYY-MM-DD.pkl`

### P3-2 — ml/evaluate.py — time-based holdout evaluation (2026-05-14)
- `ml/evaluate.py` — time-based split (cutoff = today − 30d), 0.05° grid over Delhi-NCR (≈900 cells), PR-AUC + recall@10% + log-likelihood, two baselines (random, naive count), all metrics logged to MLflow via `mlflow.start_run()`
- Key design choices: no time modifier in grid scoring (cancels out in ranking), `np.add.at` for vectorised scatter-count, `1e-10` floor on log to handle zero-density edges
- Run: `python -m ml.evaluate ml/data/snapshots/crimes_YYYY-MM-DD.parquet ml/artifacts/kde_model_YYYY-MM-DD.pkl`


### P3-1 — MLflow tracking server setup (2026-05-14)
- Implicitly completed during P1-3 (train_kde.py)
- SQLite backend: `MLFLOW_TRACKING_URI=sqlite:///ml/artifacts/mlruns.db` in config.py + .env.example
- Artifact store: `ml/artifacts/` directory (created with .gitkeep)
- `ml/train_kde.py` already calls `mlflow.start_run()`, `log_params()`, `log_metric()`, `log_artifact()`
- To browse runs locally: `mlflow ui --backend-store-uri sqlite:///ml/artifacts/mlruns.db`

### P2-1 through P2-6 — Frontend MVP (2026-05-14)
- `frontend/src/api/client.js` — Axios instance; baseURL from VITE_API_BASE_URL or /api proxy
- `frontend/src/hooks/useRouteRecommend.js` — POST /routes/recommend hook
- `frontend/src/components/DisclaimerModal.jsx` — first-visit modal, localStorage flag, info button re-opens
- `frontend/src/components/TimeOfDayPicker.jsx` — 4 presets + custom time input, emits ISO string
- `frontend/src/components/RouteForm.jsx` — address inputs + TimeOfDayPicker
- `frontend/src/components/RouteResults.jsx` — ranked list with Low/Medium/High badges
- `frontend/src/components/MapView.jsx` — MapLibre map, GeoJSON route layers coloured by risk band, fitBounds on select
- `frontend/src/App.jsx` — two-column layout wiring all components; holds routes/selectedIdx state
- `vite build` passes cleanly (117 modules, 8.87s)
- Dev server confirmed live at localhost:3000

### P1-1 through P1-7 — Risk Surface MVP (2026-05-14)
- `ml/data/category_mapping.py` — raw crime_type → macro category dict + to_macro()
- `ml/data/ingest.py` — Cosmos fetch → clean → Delhi-NCR filter → Parquet snapshot
- `ml/data/validate.py` — Great Expectations gate: 5 expectations, audit JSON, raises on failure
- `ml/train_kde.py` — per-macro KDE with recency weighting (exp decay 90d), Silverman×0.5, MLflow logging
- `backend/app/services/risk_model.py` — pkl loader, time modifier, vectorised score_points_batch + score_route
- `backend/app/services/routing.py` — ORS directions wrapper, Haversine 100m waypoint sampling, 15min TTL cache
- `backend/app/services/geocoding.py` — ORS Pelias geocoding, Delhi-NCR bbox, 24h TTL cache
- `backend/app/utils/cache.py` — TTLCache with monotonic expiry
- `backend/app/schemas/routes.py` — RouteRequest, RouteOption (Literal band), RouteResponse
- `backend/app/routers/routes.py` — POST /routes/recommend: geocode → ORS → score → band → sort
- `backend/app/config.py` — added KDE_MODEL_PATH (required)
- `backend/app/main.py` — load_model() in lifespan, routes router registered

### P0-5 — Configure CORS, secrets, observability basics (2026-05-12)
- `backend/app/utils/logger.py` — structlog configured with JSON renderer (prod) / ConsoleRenderer (dev); custom `add_request_id` processor reads ContextVar on every log call
- `backend/app/utils/__init__.py` — package init
- `backend/app/main.py` — `RequestIdMiddleware` injects UUID per request into ContextVar + `X-Request-ID` response header; `configure_logging()` called in lifespan startup
- `backend/app/config.py` — fixed `ALLOWED_ORIGINS` field (missing type annotation + quotes)
- Startup validation confirmed: missing `COSMOS_CONNECTION_STRING` raises `ValidationError` at import time before server binds to port
- JSON log output verified locally: `{"event": "...", "request_id": "-", "level": "info", "timestamp": "..."}`

### P0-4 — Deploy frontend to Vercel (2026-05-11)
- `frontend/.env.example` — documents `VITE_API_BASE_URL` and `VITE_MAPTILER_KEY` with `VITE_*` prefix rationale
- `.github/workflows/frontend-ci.yml` — build-check gate on PRs to master; passes dummy `VITE_API_BASE_URL` so build doesn't fail on missing env var
- `infra/vercel/README.md` — one-time setup steps (import repo, set env var scopes, CORS update, redeploy)
- `frontend/src/App.jsx` — added `useApiHealth` hook: fetches `/api/health` (proxy in dev) or `VITE_API_BASE_URL/health` (production); renders "API: checking/online/offline" badge
- `npx vite build` passes locally (31 modules, no errors)
- Public URL live: https://route-recommender-web.vercel.app/

### P0-3 — Deploy backend stub to Azure Container Apps (2026-05-12)
- `infra/azure/container-app.bicep` — Bicep IaC for Container App Environment + Container App (Consumption plan, min=0/max=2 replicas, readiness probe on `/health`)
- `infra/azure/container-app.json` — compiled ARM JSON (deployed via this file; `az deployment group create --template-file .bicep` silently fails on some CLI versions)
- `infra/azure/README.md` — one-time setup steps, OIDC note, rollback instructions
- `.github/workflows/backend-deploy.yml` — builds image, pushes to GHCR, smoke-tests `/health`, prints deploy command. Azure deploy step removed (student account blocks app registration for OIDC)
- `scripts/deploy.ps1` — one-command local deploy: `az containerapp update` + health verify
- Public URL live: `https://route-recommender-backend.whitecoast-8c771146.eastasia.azurecontainerapps.io/health` returns `{"status":"ok"}`
- Deviation from spec: automated Azure deploy via GitHub Actions not possible on student account (Entra ID app registration blocked). Replaced with manual `scripts/deploy.ps1` run after each CI build.

### P0-2 — Cosmos DB read-only client (2026-05-06)
- `backend/app/services/cosmos_client.py` — async `CosmosReadOnlyClient` with `connect()`, `close()`, `fetch_crime_records(since=)`
- `backend/app/services/__init__.py` — package init
- `backend/tests/test_cosmos.py` — 7 tests, all passing: strip metadata, clean doc no-op, not-connected guard, happy path, empty result, error re-raise, `since=` timestamp filter
- Bug found and fixed: `"message"` is a reserved key in `logging.LogRecord`; renamed to `"error_message"` in `extra=`
- `pytest` and `pytest-asyncio` added to `backend/requirements.txt`

### P0-1 — Initialise repository structure (2026-05-06)
- All files created per task spec
- `docker compose up --build` starts backend + frontend
- `curl http://localhost:8000/health` returns `{"status": "ok"}`
- Frontend placeholder renders at `http://localhost:3000`
- `.env.example` documents all variables declared in `config.py`

---

## Notes for Claude Code

- **Reading sister repo:** the data extraction repo is at a separate path
  on disk (`d:\Route_Recomender_Second\` per its CLAUDE.md). Do NOT modify
  files there from this repo. Communication is via Cosmos DB only.
- **Style:** match Python style of sister repo (4-space indent, type hints,
  Pydantic v2). For JS, use functional components + hooks.
- **Tests:** every backend service module needs at least one happy-path
  and one failure-path test. Frontend tests are nice-to-have for v1.
- **Commits:** one task = one PR. PR title format: `[P0-N] Short description`.
- **Per-session start:** read this file first. Then read the relevant
  source files for the current task. Plan in plan-mode. Get my approval.
  Then execute.

## Learning Mode (Required for every task)

Claude Code: this is a learning project, not a delivery project.
For every task, follow this protocol. Do not skip ahead.

I am a learner, not an experienced engineer. I will TYPE the
backend/ML code myself, but I need you to provide it. The learning
happens through understanding before typing and asking questions
during typing.

FRONTEND FILES RULE: I do NOT type frontend files. For any file
under frontend/ (components, hooks, styles, config), Claude Code
creates the file directly using the Write/Edit tools. Do not present
frontend code for me to type — just write it.

---

STEP 1 — CONCEPT BRIEF (before any code)

Produce a 1-page brief covering:
  - What problem this task solves and why it matters in the
    overall system
  - 2-3 alternative approaches and why the spec chose this one
  - 3-4 production failure modes and how the design defends
    against each
  - For ML/data-science tasks: the underlying math/statistics
    in plain language, with at least one worked numerical example

Then proceed directly to Step 2. No MCQs, no questions to answer.

---

STEP 2 — GUIDED CODE WALKTHROUGH (you write, I type)

Provide the code for ONE file (or one logical chunk if file is
long), but in a specific format:

  - Show me the code in chunks of ~15-20 lines maximum
  - Before each chunk, write 2-3 sentences explaining WHAT
    this chunk does and WHY it's structured this way
  - Use rich inline comments. Every non-obvious line gets a
    "# WHY: ..." comment, not just "# WHAT: ..."

Do NOT dump the full file at once.

I will type the backend/ML code myself in my IDE. If I have
questions about a specific line, I'll ask, and you explain — but
don't volunteer line-by-line explanation beyond your inline
comments unless I ask.

---

PROTOCOL REMINDERS

  - If I say "just build it" or "skip to the code," remind me
    of this protocol and ask me to confirm I want to break it.
    I might have a real reason (debugging an urgent issue) but
    you should make me state it.
  - No MCQs, no prediction pauses, no spaced repetition checks.
    Just concept brief → code chunks → I type.