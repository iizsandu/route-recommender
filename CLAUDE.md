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

**Current state:** Empty repo. Sister repo Cosmos DB has ~5,000 valid
Delhi-NCR crime records (out of ~8,000 total — the rest are non-crime
articles or outside Delhi).

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
│   │   ├── main.py                 # App entrypoint, CORS, lifespan
│   │   ├── config.py               # Pydantic Settings, env-based
│   │   ├── dependencies.py         # Shared singletons (Cosmos client, KDE model)
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── health.py           # GET /health
│   │   │   ├── routes.py           # POST /routes/recommend
│   │   │   └── risk.py             # GET /risk/cell, GET /risk/heatmap
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── cosmos_client.py    # Async read-only Cosmos client
│   │   │   ├── routing.py          # OpenRouteService wrapper + caching
│   │   │   ├── geocoding.py        # Address → lat/lng (Nominatim or ORS)
│   │   │   ├── risk_model.py       # KDE risk surface (loads pickled model)
│   │   │   └── route_scorer.py     # Aggregate route risk from waypoints
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── routes.py           # RouteRequest, RouteResponse, RouteOption
│   │   │   └── risk.py             # RiskQuery, RiskResponse
│   │   ├── models/                 # Pickled risk_model.pkl loaded on startup
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── logger.py           # structlog
│   │       └── cache.py            # In-memory TTL cache for routes/geocoding
│   └── tests/
│       ├── test_risk_model.py
│       ├── test_route_scorer.py
│       ├── test_routing.py
│       └── conftest.py
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
│   ├── train_kde.py                # Build KDE model from Cosmos snapshot
│   ├── train_lightgbm.py           # Phase 4 — LightGBM risk classifier
│   ├── evaluate.py                 # Time-based holdout, PR-AUC, recall@10%
│   ├── promote_model.py            # Champion/challenger gate logic
│   ├── data/
│   │   ├── ingest.py               # Cosmos → DataFrame → Parquet snapshot
│   │   └── validate.py             # Great Expectations data quality
│   ├── notebooks/                  # Exploratory only, not part of pipeline
│   └── artifacts/                  # MLflow + pickled models output here
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
        ├── backend-ci.yml          # Lint, test, build on PR
        ├── backend-deploy.yml      # Deploy to Azure Container Apps on main
        ├── frontend-ci.yml         # Lint, test, build on PR
        └── retrain-weekly.yml      # Weekly cron: retrain KDE/LightGBM
```

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

## Active Sprint — Phase 0: Bootstrap & Deploy

> **Claude Code instruction:** Work tasks in order. Complete one task fully
> (code + tests + deployed if applicable) before starting the next. Move
> completed tasks to the Sprint Completed Log at the bottom of this file.

### Current Task

**TASK P0-4 — Deploy frontend to Vercel**

---

### Upcoming Tasks (do not start until current is done)

**TASK P0-2 — Cosmos DB read-only client**

**Files:** `backend/app/services/cosmos_client.py`, `backend/tests/test_cosmos.py`

- Async client using `azure-cosmos`
- Read-only — never write
- Method: `async def fetch_crime_records(since: datetime | None = None) -> list[dict]`
- Returns flat dicts, strips Cosmos metadata (`_rid`, `_self`, `_etag`, etc.)
- Uses connection string from env (`COSMOS_CONNECTION_STRING`)
- Configurable container name via env (default: `structured_crimes`)
- Test: mock the Cosmos response, assert metadata stripped, assert empty
  result handled

**Do NOT:**
- Implement caching here (separate concern, comes in P0-4)
- Filter by Delhi bounds here — that's the model layer's job

---

**TASK P0-3 — Deploy backend stub to Azure Container Apps**

**Files:** `infra/azure/container-app.bicep`, `.github/workflows/backend-deploy.yml`,
`infra/azure/README.md`

- Bicep template provisions:
  - Container App Environment (consumption plan, free tier)
  - Container App with min=0, max=2 replicas (scale to zero)
  - Ingress public, port 8000
  - Secrets for env vars (Cosmos connection string at minimum)
- GitHub Action triggers on push to `main`:
  - Build Docker image
  - Push to Azure Container Registry (or GHCR)
  - Deploy to Container App via `az containerapp update`
- README documents one-time manual setup steps (creating ACR, service
  principal for GitHub Actions, secret names)

**Acceptance:**
- Public URL responds 200 to `/health`
- Cold start under 10 seconds (acceptable for portfolio)
- GitHub Action runs end-to-end on a test commit

---

**TASK P0-4 — Deploy frontend to Vercel**

**Files:** `infra/vercel/README.md`, `frontend/.env.example`,
`.github/workflows/frontend-ci.yml`

- Connect Vercel to GitHub repo (manual one-time)
- Auto-deploy on push to `main`
- Env var: `VITE_API_BASE_URL` points to Azure Container App URL
- Frontend health check: shows "API: online" badge if backend `/health` reachable

**Acceptance:**
- Public URL renders "Hello World" page
- Frontend can hit backend `/health` (CORS configured correctly)

---

**TASK P0-5 — Configure CORS, secrets, observability basics**

**Files:** `backend/app/main.py`, `backend/app/utils/logger.py`

- CORS: only allow Vercel preview URLs + the production frontend URL
- `structlog` configured for JSON logs in production, human-readable in dev
- All env vars validated at startup via Pydantic Settings — service refuses
  to start if `COSMOS_CONNECTION_STRING` is missing
- Add request-ID middleware for tracing

**Acceptance:**
- Frontend on Vercel can call backend (no CORS errors)
- Logs in Azure Container App stream show structured JSON
- Starting backend without `COSMOS_CONNECTION_STRING` fails fast with
  a clear error

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

# MapTiler
VITE_MAPTILER_KEY=your_key_here

# MLflow
MLFLOW_TRACKING_URI=sqlite:///ml/artifacts/mlruns.db
MLFLOW_REGISTRY_URI=sqlite:///ml/artifacts/mlruns.db

# CORS
ALLOWED_ORIGINS=http://localhost:3000,https://route-recommender.vercel.app

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
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
happens through (a) understanding before typing, (b) active prediction
during typing, and (c) defending after typing.

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



  - Data-science / ML tasks (any task in ml/, risk_model.py,
    route_scorer.py): questions must be DATA SCIENCE focused —
    statistics, model behaviour, bias/variance, evaluation
    metrics, feature engineering, data quality, edge cases in
    the math
  - Infra / deployment tasks (Phase 0, infra/, .github/workflows/):
    MLOps and systems questions — CI/CD, containers, secrets,
    scaling, observability, cost
  - Backend API tasks (routers, services not in ML pipeline):
    a mix — API design, error handling, caching, plus DS
    questions about WHY the API shape serves the model

Wait for my answers (I reply with the letter, e.g. "B"). Do not
proceed until I've answered all five. If an answer is wrong, tell
me why and ask me to try again before moving on. Do not accept
silence — give me a hint if I'm stuck.

---

STEP 2 — GUIDED CODE WALKTHROUGH (you write, I type)

Provide the code for ONE file (or one logical chunk if file is
long), but in a specific format:

  - Show me the code in chunks of ~15-20 lines maximum
  - Before each chunk, write 2-3 sentences explaining WHAT
    this chunk does and WHY it's structured this way
  - Use rich inline comments. Every non-obvious line gets a
    "# WHY: ..." comment, not just "# WHAT: ..."
    "Before I show the next chunk — what happens if [edge case]?"
  - Wait for my answer (the letter). Then show the next chunk
    and tell me whether I was right and why.

This is non-negotiable: do NOT dump the full file at once. The
prediction-pause is the entire point — it keeps me from
transcribing on autopilot.

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
  - If I'm answering MCQs correctly with no effort, raise the
    difficulty. Easy quizzes teach nothing.
  - At the start of every session, ask me what I remember from
    the last "learning_log.md" entry. Spaced repetition matters.
  - Once a week (Sunday), pick a random completed task and
    quiz me cold on it. If I've forgotten, we revisit.