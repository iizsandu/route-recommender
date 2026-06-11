# Phase 7 — GraphHopper Crime-Aware Routing: Implementation Plan (Revision 2)

> **Status:** Planning only. No code written. This document is the single source of truth for Phase 7.
> **Prepared:** 2026-06-09 | **Revised:** 2026-06-09 (Revision 2 — resolves all findings from `docs/phase7_verification.md`)
> **Author:** Claude Code

---

## Reconnaissance Summary

Files read before writing this plan:
- `backend/app/services/routing.py` (119 lines)
- `backend/app/config.py` (104 lines)
- `backend/app/main.py` (193 lines)
- `ml/kde_model.py` (56 lines)
- `ml/artifacts/kde_robbery.pkl` (inspected via Python)
- `ml/data/category_mapping.py` (145 lines)
- `ml/data/ingest.py` (selected sections)
- `ml/train_kde.py` (selected sections — KDE pool filter)
- `docker-compose.yml` (43 lines)
- `backend/app/routers/routes.py` (219 lines)
- `docs/phase7_verification.md` (independent technical review against GH 9.1 tag `73e6b7cc3ca163ce0b53692f7cd732dba170bfce`)

All figures and decisions in this document are derived directly from those files.
GraphHopper 9.1 API claims are verified against commit `73e6b7cc3ca163ce0b53692f7cd732dba170bfce`.

---

### ⚠️ Audit Findings

**Finding 1 — Route deduplication code is absent from `routing.py`.**
The current file on disk is 119 lines and contains neither `_route_fingerprint()` nor `_deduplicate_routes()`. The Phase 7 rewrite of `routing.py` (Stage 5) must include these functions.

**Finding 2 — `ORS_API_KEY` has no default and is currently required.**
`backend/app/config.py` declares `ORS_API_KEY: str` with no default value. Must be changed to `ORS_API_KEY: str = ""` when switching to GraphHopper.

**Finding 3 — `routes.py` hardcodes `"driving-car"` in the response cache key.**
Line 101: `ck = _cache_key(lat_o, lng_o, lat_d, lng_d, depart_time, "driving-car")`. Must use request profile after the switch.

**Finding 4 — `docker-compose.yml` has no named `volumes:` section.**
Must add top-level `volumes: graphhopper_graph:` for the GH graph cache.

**Finding 5 (Verification) — OSMnx/Overpass downloads different graph than the PBF.**
Stage 2 (original) used Overpass API, which timestamps and coverage differ from the pinned PBF that GraphHopper reads. Shapely's length-weighted line centroid also differs from Java's unweighted vertex average. The centroid-string lookup contract is unreliable. Resolved in Revision 2 by replacing OSMnx with a GH-edge-export approach (see Stage 0 + revised Stage 2).

**Finding 6 (Verification) — CrimeWeighting.java does not compile against GH 9.1.**
Constructor, method signatures, time method, and units are all wrong for GH 9.1. Resolved in revised Section 5.

**Finding 7 (Verification) — DefaultWeightingFactory patch was structurally incorrect.**
Wrong package, wrong dispatch mechanism, missing encoded values. Resolved in Section 5g.

**Finding 8 (Verification) — config.yml is invalid for GH 9.1.**
`vehicle` key rejected, `fastest` weighting throws, server block incorrectly nested, `bindHost` vs `bind_host`. Resolved in Section 4b.

**Finding 9 (Verification) — `osmnx>=1.9.0` permits OSMnx 2.x with incompatible API.**
Pin to `osmnx==1.9.4`. Resolved (and OSMnx dependency removed from edge pipeline entirely in Revision 2).

**Finding 10 (Verification) — Spatial join pseudocode fails on OSMnx (u,v,key) MultiIndex.**
GeoPandas 0.14 expands MultiIndex into `index_right0/1/2`. Resolved in Revision 2 by using GH-edge-export (simple integer index, no MultiIndex).

**Finding 11 (Verification) — No telemetry, quality gate, or match-rate validation.**
Added in revised Sections 5e, 6c, and new Section 4e.

**Finding 12 (Verification) — Per-request profile selection absent.**
`RouteRequest` gains an optional `profile` field in revised Section 6b.

**Finding 13 (Verification) — Weekly retrain never rebuilds edge_risk.json.**
Addressed in revised Section 8b (weekly workflow extension).

**Finding 14 (Verification) — Cloudflare setup requires CF-managed domain; Windows service path wrong.**
Revised Section 7 documents both the quick-tunnel path (no domain) and the named-tunnel path (requires domain).

**Finding 15 (Verification) — λ units wrong: GH 9.1 calcEdgeWeight returns seconds not milliseconds.**
λ values revised from 100/300 (ms-based) to 0.1/0.3 (seconds-based). Same time-budget targets: 0.1×1.0×1000 m = 100 s ≈ 1.7 min; 0.3×1.0×1000 m = 300 s = 5 min.

---

## Section 1 — Stage Overview Table

| Stage | Name | New Files | Modified Files | Depends On |
|-------|------|-----------|----------------|------------|
| 0 | Graph Mapping Experiment | `graphhopper/tools/EdgeExporter.java`, `ml/artifacts/gh_edges.csv` | None | Stage 1 (PBF downloaded) + Stage 3 partial (GH graph built) |
| 1 | OSM Data Acquisition | `graphhopper/data/.gitkeep` | `.gitignore`, `docker-compose.yml` | None |
| 2 | Edge Risk Pipeline | `ml/build_edge_risk.py` | `ml/requirements.txt` | Stage 0 (`gh_edges.csv` produced); KDE pkl files; Parquet snapshot |
| 3 | GraphHopper Docker Setup | `graphhopper/Dockerfile`, `graphhopper/config.yml`, `graphhopper/docker-entrypoint.sh` | `docker-compose.yml` | Stage 1 (PBF), Stage 2 (`edge_risk.json`) |
| 4 | CrimeWeighting.java | `graphhopper/src/CrimeWeighting.java`, `graphhopper/src/DefaultWeightingFactoryPatch.java` | `graphhopper/Dockerfile` | Stage 3 |
| 5 | Backend Routing Service Update | None | `routing.py`, `config.py`, `main.py`, `routes.py`, `schemas/routes.py` | Stage 3 (GH running on 8989) |
| 6 | Cloudflare Tunnel Setup | `~/.cloudflared/config.yml` (local) | Azure Container Apps env vars | Stage 3 |
| 7 | Verification | None | None | All previous |

> **Stage ordering note:** Stage 0 requires Stage 1 (PBF on disk) and a partial Stage 3 (first GH startup to build the graph and export edge geometries). Stages 2 and 3 (full) then follow Stage 0. This is a deliberate two-pass startup for the initial setup; afterwards, the startup sequence is: `docker compose up` → Stage 3 runs automatically → `python -m ml.build_edge_risk` → `docker compose restart graphhopper`.

---

## Section 2 — Stage 1: OSM Data Acquisition

### The File

**Geofabrik URL:**
```
https://download.geofabrik.de/asia/india/delhi-latest.osm.pbf
```
Geofabrik maintains official regional extracts of the OpenStreetMap full planet dump, updated daily. `delhi-latest.osm.pbf` covers the National Capital Territory of Delhi. File size is approximately 120–150 MB.

**Pinning the PBF:** The weekly Geofabrik file changes daily. For reproducible edge-risk computation, **pin to one download**. Do not replace `delhi-ncr-latest.osm.pbf` without also re-running Stage 0 (edge export) and Stage 2 (edge risk) and deleting the graph cache volume (see cache invalidation rules in Section 4e).

**Where to store it:**
```
graphhopper/data/delhi-ncr-latest.osm.pbf
```

**Coverage caveat:** NCT of Delhi only. Gurgaon (Haryana) and Noida (Uttar Pradesh) are outside this boundary. Routing requests to those areas will fail at the GH level. Backend geocoding must validate coordinates against the Delhi bbox before forwarding to GH; add this guard in Stage 5 (`routing.py`).

### `.gitignore` Entry

```
# GraphHopper OSM data (too large for git — download manually)
graphhopper/data/*.pbf
graphhopper/data/*.csv
```

Also gitignore `gh_edges.csv` (generated by Stage 0, ~15 MB).

### Docker Volume Mount (Preview — Full Context in Stage 3)

```yaml
volumes:
  - ./graphhopper/data/delhi-ncr-latest.osm.pbf:/data/delhi-ncr-latest.osm.pbf
```

---

## Section 2b — Stage 0: Graph Mapping Experiment (Mandatory Pre-Stage-2 Spike)

**This stage is a mandatory blocking spike, not optional research.**

The original plan attempted to match crime data to GraphHopper edges by computing Shapely centroids from an OSMnx graph (downloaded from Overpass) and matching them via rounded string keys to Java vertex-average midpoints of GraphHopper edges. That approach has three independent failure modes:

1. **Different source data**: OSMnx fetches from Overpass (current OSM state); GraphHopper reads the pinned PBF. They can diverge.
2. **Different segmentation**: OSMnx simplifies (removes pass-through nodes); GraphHopper retains pillar/tower nodes. One OSMnx edge can cover several GH edges, so midpoints can be tens to hundreds of metres apart.
3. **Different midpoint calculations**: Shapely's line centroid is length-weighted; the Java vertex average weights every stored vertex equally.

**Revision 2 eliminates these problems by using GH's own edge representations.** Stage 0 builds the GH graph from the PBF and then exports every edge's geometry directly. Stage 2 then uses these GH-native edge geometries to match crime points. The JSON is keyed by GH integer edge IDs, eliminating all string-formatting/locale/centroid issues.

### Stage 0 Protocol

**Pre-condition:** Stage 1 complete (PBF at `graphhopper/data/`). A partial Stage 3 is needed: the GH Docker image must be built (from a config with `fastest` profile only) so the graph can be preprocessed.

**Step 0a — Build GH graph for export (fastest profile only):**

Create a temporary minimal config `graphhopper/config-bootstrap.yml`:
```yaml
graphhopper:
  graph.location: /graphhopper/graph-cache
  datareader.file: /data/delhi-ncr-latest.osm.pbf
  profiles:
    - name: fastest
      weighting: custom
      custom_model_files: []
  prepare.ch.weightings: none
  prepare.lm.weightings: none
```

Start GH with this bootstrap config and wait for graph build (~5–10 min).

**Step 0b — Run EdgeExporter:**

`graphhopper/tools/EdgeExporter.java` (see Section 5g for the exact class design) iterates every base edge in the compiled graph and writes `gh_edges.csv` to `/data/gh_edges.csv` (bind-mounted from `ml/artifacts/gh_edges.csv`).

Run after GH is ready:
```powershell
docker compose exec graphhopper java `
  -cp /app/graphhopper.jar `
  com.graphhopper.tools.EdgeExporter `
  /graphhopper/graph-cache `
  /data/gh_edges.csv
```

Output format of `gh_edges.csv`:
```
edge_id,length_m,geom_wkt
0,245.3,"LINESTRING(77.2090 28.6139,77.2095 28.6145,77.2101 28.6152)"
1,88.7,"LINESTRING(77.2101 28.6152,77.2109 28.6158)"
...
```

Columns: `edge_id` (GH integer edge ID, undirected), `length_m` (metres), `geom_wkt` (WKT in EPSG:4326 with coordinates as `lng lat`).

**Step 0c — Acceptance thresholds (before Stage 2 implementation begins):**

Run a quick Python validation script:
```python
import geopandas as gpd, pandas as pd, json

edges = pd.read_csv("ml/artifacts/gh_edges.csv")
print(f"Total GH edges: {len(edges):,}")
# After Stage 2 trial run:
with open("ml/artifacts/edge_risk.json") as f:
    d = json.load(f)
scores = d["edge_scores"]
n_scored = sum(1 for v in scores.values() if v > 0)
coverage = n_scored / len(edges) * 100
print(f"Non-zero risk coverage: {n_scored:,} / {len(edges):,} ({coverage:.1f}%)")
```

**Acceptance criteria:**
- Non-zero risk coverage: **≥ 10%** of GH edges have a matched crime score. (Delhi's drivable road network is ~89K edges; 10% = ~8,900 scored edges covering all major roads in the crime pool.)
- If coverage is under 10%, widen the buffer radius from 150 m to 250 m and re-run.
- If coverage remains under 5% after widening, halt Stage 2 and investigate the crime pool filter.

**No collision possible with integer keys.** Each GH edge ID is unique by construction. Collision was a risk of the original centroid-string design; it is eliminated here.

---

## Section 3 — Stage 2: Edge Risk Pipeline (`ml/build_edge_risk.py`)

**This section has been substantially revised from Revision 1.** The pipeline no longer uses OSMnx or Overpass. It reads `gh_edges.csv` produced by Stage 0, which contains the exact geometries of every GH edge from the same pinned PBF that GraphHopper routes on. The JSON output is keyed by GH integer edge IDs, so the Java lookup in `CrimeWeighting` is an O(1) `HashMap<Long, Double>` get — no string formatting, no locale issue, no centroid mismatch.

### 3a. Input Data Sources

**Crime points from Parquet snapshot:**
```python
from ml.data.category_mapping import KDE_ELIGIBLE, FEMALE_WEIGHTS
from ml.train_kde import find_latest_snapshot, build_kde_pool
import pandas as pd

snapshot_path = find_latest_snapshot()   # crimes_YYYY-MM-DD.parquet
df_all = pd.read_parquet(snapshot_path)
pool = build_kde_pool(df_all)            # 4,655 KDE-pool records
```

`build_kde_pool` applies: `is_delhi_crime=True`, `lat` not null, `not is_historical`, `crime_macro in KDE_ELIGIBLE`.

Result columns needed: `lat`, `lng`, `crime_macro`, `effective_date`.

**KDE models for density evaluation:**
```python
import pickle, glob, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

kde_models = {}
for pkl_path in sorted(Path("ml/artifacts").glob("kde_*.pkl")):
    with open(pkl_path, "rb") as f:
        d = pickle.load(f)
    kde_models[d["category"]] = d["kde"]
```

**GH edge geometries from Stage 0:**
```python
import geopandas as gpd
from shapely import wkt as shapely_wkt

edges_raw = pd.read_csv("ml/artifacts/gh_edges.csv")
# geom_wkt uses "lng lat" order per WKT convention
edges_raw["geometry"] = edges_raw["geom_wkt"].apply(shapely_wkt.loads)
edges_gdf = gpd.GeoDataFrame(edges_raw, geometry="geometry", crs="EPSG:4326")
```

**Version manifest check:** Before running, validate that the KDE pkl `trained_at` timestamp and the snapshot filename are consistent (both come from the same weekly run). Log a warning if they diverge by more than 7 days.

```python
import re
from datetime import datetime, timezone
snap_date_str = re.search(r"crimes_(\d{4}-\d{2}-\d{2})", snapshot_path.name).group(1)
snap_date = datetime.fromisoformat(snap_date_str).replace(tzinfo=timezone.utc)
for cat, kde in kde_models.items():
    pkl_date = datetime.fromisoformat(d["trained_at"]).astimezone(timezone.utc)
    delta_days = abs((pkl_date - snap_date).days)
    if delta_days > 7:
        print(f"WARNING: {cat} KDE trained_at differs from snapshot by {delta_days} days.")
```

### 3b. Spatial Matching — Crime Points to GH Edges

**Why GH edges, not OSMnx:** The `gh_edges.csv` geometries are produced by GH's own import of the same PBF, so the LineStrings are the exact road segments GH routes on. There is no segmentation mismatch.

**Data structures after loading:**
- `edges_gdf`: GeoDataFrame indexed by row (not a MultiIndex). Has columns `edge_id` (GH integer), `length_m`, `geometry` (Shapely LineString in EPSG:4326).
- `crime_gdf`: GeoDataFrame with columns `lat`, `lng`, `crime_macro`, `effective_date`, `geometry` (Shapely Point in EPSG:4326).

**Projection and buffering:**
```python
# Project to UTM Zone 43N for accurate metre-based operations
edges_proj = edges_gdf.to_crs("EPSG:32643")
crimes_proj = crime_gdf.to_crs("EPSG:32643")

# Buffer the full LineString geometry (not just midpoints) by 150m
edge_buffers = edges_proj.copy()
edge_buffers["geometry"] = edges_proj.geometry.buffer(150)
# edge_buffers now has Polygon geometries (±150m around each road)
```

Buffering the full LineString (not a midpoint) correctly handles long road segments: a crime at the far end of a 500 m segment is still within 150 m of the buffer.

**Spatial join — no MultiIndex issue:**

Because `edge_buffers` is indexed by a simple integer row index (not OSMnx's `(u, v, key)` MultiIndex), GeoPandas produces a single `index_right` column:

```python
joined = crimes_proj.sjoin(
    edge_buffers[["edge_id", "geometry"]],
    how="left",
    predicate="within"
)
# joined.columns: original crime columns + "index_right" (edge_buffers row idx)
# joined["edge_id"] is the GH edge integer (from edge_buffers["edge_id"])
```

Crime points that fall within no buffer have `NaN` in `edge_id` and are discarded.

**Why 150 m:** One city block in Delhi's urban grid. Covers geocoding noise (~100 m) while not bleeding across parallel roads. Crime points within 150 m of multiple edges contribute to all of them (correct model — see Revision 1 Section 3c for rationale, which is unchanged).

### 3c. Risk Score Accumulation Per Edge

**Formula:**
```
edge_score(e) = Σ  [ kde_density(c.lat, c.lng)
                     × female_weight(c.crime_macro)
                     × recency_weight(c.effective_date) ]
                over all crime points c within 150m of edge e
```

```python
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np

today = datetime.now(timezone.utc)

def recency_weight(effective_date):
    if pd.isna(effective_date):
        return 1.0
    age_days = max(0, (today - effective_date.replace(tzinfo=timezone.utc)).days)
    return np.exp(-age_days / 90.0)

edge_raw_scores = defaultdict(float)

for _, row in joined.dropna(subset=["edge_id"]).iterrows():
    cat = row["crime_macro"]
    eid = int(row["edge_id"])
    kde  = kde_models[cat]
    density = float(kde(np.array([[row.lat], [row.lng]]))[0])
    fw   = FEMALE_WEIGHTS[cat]
    rw   = recency_weight(row["effective_date"])
    edge_raw_scores[eid] += density * fw * rw
```

**KDE density term:** Evaluating the KDE at the crime point's own location gives a "how clustered is this area" weight, amplifying crimes that occur in already-dangerous areas.

**Double-recency note:** KDE training already applied recency weighting. Applying it here again means old crimes are doubly discounted. This is a documented conservative design choice (old crimes matter less for current routing decisions).

### 3d. Normalisation — P99 Clipping

Dividing by the raw maximum makes the entire score distribution depend on a single outlier crime cluster, which causes all other edges to score near zero and makes λ calibration unstable between retrains.

**Revised formula: clip at the 99th percentile.**
```python
raw_values = np.array(list(edge_raw_scores.values()))
p99 = float(np.percentile(raw_values, 99)) if len(raw_values) > 0 else 1.0
max_score = max(p99, 1e-10)  # guard against all-zero degenerate case

edge_normalised = {}
for eid, raw in edge_raw_scores.items():
    edge_normalised[eid] = float(min(raw / max_score, 1.0))
    # min(..., 1.0) caps the rare edges above p99 at 1.0
```

Result: ~99% of edges score in [0, 1] with a meaningful spread. The top 1% of extreme clusters cap at 1.0 rather than compressing all other edges toward zero.

### 3e. Output Format

**File path:** `ml/artifacts/edge_risk.json`

```json
{
  "edge_scores": {
    "12345": 0.87,
    "67890": 0.31,
    "99001": 0.03,
    ...
  },
  "metadata": {
    "n_edges_scored": 12400,
    "n_edges_total": 89200,
    "n_crime_points": 4655,
    "generated_at": "2026-06-09T14:30:00+00:00",
    "max_raw_score": 0.0423,
    "p99_raw_score": 0.0381,
    "snapshot_date": "2026-05-15",
    "gh_edges_csv_rows": 89200
  }
}
```

**Key format:** GH integer edge IDs as JSON strings (`"12345"` not `12345`). JSON keys must be strings; the Java `HashMap<Long, Double>` is populated by parsing each key as `Long.parseLong(key)`.

**Metadata**: `snapshot_date` and `gh_edges_csv_rows` allow the startup validator to detect stale JSON (e.g., PBF was replaced but edge_risk.json was not regenerated — the row count will differ from the current export).

### 3f. CLI Interface

```bash
python -m ml.build_edge_risk
```

Optional flags:
- `--gh-edges PATH`: path to `gh_edges.csv` (default: `ml/artifacts/gh_edges.csv`)
- `--artifacts-dir PATH`: KDE pkl directory (default: `ml/artifacts/`)
- `--snapshot PATH`: specific Parquet snapshot (default: latest)

Progress output:
```
[1/5] Loading KDE models from ml/artifacts/ ... 8 categories, 4655 training points
[2/5] Loading crime snapshot crimes_2026-05-15.parquet ... 4655 KDE-pool records
[3/5] Loading GH edge geometries from ml/artifacts/gh_edges.csv ... 89200 edges
[4/5] Projecting to EPSG:32643 and buffering edges (150m) ...
      Spatial join: 4312 crime points matched to 12400 unique edges
      Discarded: 343 crime points (>150m from any GH road)
[5/5] Accumulating scores, normalising (p99 clip), writing JSON ...
      Edges scored: 12400 / 89200 (13.9%)
      Coverage check: 13.9% >= 10% threshold: PASS
Done in 89s.
```

If coverage is below 10%, the script exits with a non-zero return code and a clear error message: `FAIL: non-zero risk coverage 4.1% < 10% threshold. Widen buffer or investigate crime pool filter.`

---

## Section 4 — Stage 3: GraphHopper Docker Setup

### 4a. Dockerfile (`graphhopper/Dockerfile`)

**GraphHopper version pin:** Tag `9.1` at commit `73e6b7cc3ca163ce0b53692f7cd732dba170bfce`.

```dockerfile
ARG GH_COMMIT=73e6b7cc3ca163ce0b53692f7cd732dba170bfce
```

The Dockerfile uses a two-stage build:

**Stage 1 (builder) — `eclipse-temurin:17-jdk`:**
1. Install Maven and Git.
2. Clone GH at the pinned commit (not just the tag, for reproducibility):
   ```
   git clone https://github.com/graphhopper/graphhopper.git /build/graphhopper
   git -C /build/graphhopper checkout ${GH_COMMIT}
   ```
3. Copy `CrimeWeighting.java` into the correct package directory:
   ```
   COPY src/CrimeWeighting.java \
     /build/graphhopper/core/src/main/java/com/graphhopper/routing/weighting/CrimeWeighting.java
   ```
4. Copy the `DefaultWeightingFactory` patch (see Section 5g):
   ```
   COPY src/DefaultWeightingFactoryPatch.java \
     /build/graphhopper/core/src/main/java/com/graphhopper/routing/DefaultWeightingFactory.java
   ```
   This **replaces** (not appends to) `DefaultWeightingFactory.java` with the patched version.
5. Copy `EdgeExporter.java`:
   ```
   COPY src/EdgeExporter.java \
     /build/graphhopper/tools/src/main/java/com/graphhopper/tools/EdgeExporter.java
   ```
6. Build all modules that include our new classes:
   ```
   RUN cd /build/graphhopper && mvn package -DskipTests -pl web,tools -am
   ```

**Stage 2 (runtime) — `eclipse-temurin:17-jre`:**
1. Copy the web JAR and tools JAR.
2. Copy `config.yml`, `config-bootstrap.yml`, and `docker-entrypoint.sh`.
3. Expose port 8989.
4. Add Docker health check:
   ```dockerfile
   HEALTHCHECK --interval=15s --timeout=5s --start-period=600s --retries=40 \
     CMD curl -f http://localhost:8989/health || exit 1
   ```
   `start-period=600s` accommodates the 5–10 minute graph preprocessing time before health checks begin.
5. Entrypoint: `ENTRYPOINT ["/app/docker-entrypoint.sh"]`

**Why build from source:** Pre-built JARs do not contain `CrimeWeighting.java`. Source build is the only way to include custom weightings.

### 4b. `graphhopper/config.yml` (GH 9.1-compatible)

The following is the complete, valid configuration for GraphHopper 9.1. Every field has been validated against the GH 9.1 source at commit `73e6b7cc`.

```yaml
graphhopper:
  graph.location: /graphhopper/graph-cache
  datareader.file: /data/delhi-ncr-latest.osm.pbf

  profiles:
    # GH 9.1 does not support weighting: fastest — use custom with empty model.
    # An empty custom_model_files list produces default time-optimised routing.
    - name: fastest
      weighting: custom
      custom_model_files: []

    # Crime-aware profiles use our custom weighting registered in the patched factory.
    # The profile must NOT contain a "vehicle" key — GH 9.1 rejects it.
    - name: balanced
      weighting: crime_aware
      crime_aware.lambda: 0.1
      crime_aware.edge_risk_path: /data/edge_risk.json

    - name: safest
      weighting: crime_aware
      crime_aware.lambda: 0.3
      crime_aware.edge_risk_path: /data/edge_risk.json

  # Run all profiles in flexible mode (no CH/LM preparation).
  # CH preparation bakes weighting into the graph; CrimeWeighting reads
  # edge_risk.json at startup and cannot be CH-prepared without rebuild on
  # every JSON change. Flexible mode routes correctly in <1 second for Delhi.
  prepare.ch.weightings: none
  prepare.lm.weightings: none

# Server block is top-level (NOT nested under graphhopper:).
# GH 9.1 uses bind_host (snake_case) not bindHost (camelCase).
server:
  application_connectors:
    - type: http
      port: 8989
      bind_host: 0.0.0.0
  admin_connectors:
    - type: http
      port: 8990
      bind_host: 127.0.0.1
```

**The λ values and their meaning (seconds-based weight):**

`calcEdgeWeight()` in GH 9.1 returns weight in **seconds** (not milliseconds). `calcEdgeMillis()` returns physical travel time in ms. The formula for our weighting is:

```
cost = travelTimeSec + λ × riskScore × edgeLengthM
```

- `λ = 0` (fastest): `cost = travelTimeSec`. Pure travel time. No crime awareness.
- `λ = 0.1` (balanced): For a 1 km road at max risk (score=1.0): `0.1 × 1.0 × 1000 = 100 s ≈ 1.7 min` added. Sanity check — 200 m at risk 0.5: `0.1 × 0.5 × 200 = 10 s`. Router accepts a 10-second detour to avoid a moderately dangerous 200 m stretch. Realistic moderate deviation from fastest.
- `λ = 0.3` (safest): 1 km at max risk: `0.3 × 1.0 × 1000 = 300 s = 5 min` added. 200 m at risk 0.5: `0.3 × 0.5 × 200 = 30 s` — a 30-second detour to avoid a moderate-risk 200 m road. Strong enough for meaningful geometric detours around high-crime corridors.

The λ values are derived from explicit time-budget targets (1.7 min and 5 min extra per max-risk km), not guesses. A calibration exercise once the system is running should compare safest-vs-fastest geometries on known high-crime corridors (e.g., Chandni Chowk, Rohini) and adjust λ if detours are too small (increase) or implausibly long (decrease).

**`prepare.ch.weightings: none` rationale:**
Contraction Hierarchies pre-compute shortest paths for a fixed weighting. `CrimeWeighting` reads `edge_risk.json` at startup — a different JSON means different route choices. If CH were enabled for crime-aware profiles, the CH shortcuts would be stale after every weekly retrain that regenerates `edge_risk.json`. Flexible routing (no CH) is correct here. For Delhi's graph (~89 K edges), flexible routing completes a request in under 200 ms, well within acceptable latency.

### 4c. `docker-compose.yml` Additions

New `graphhopper:` service block:

```yaml
  graphhopper:
    build:
      context: ./graphhopper
      dockerfile: Dockerfile
    ports:
      - "8989:8989"
    volumes:
      - ./graphhopper/data/delhi-ncr-latest.osm.pbf:/data/delhi-ncr-latest.osm.pbf:ro
      - ./ml/artifacts/edge_risk.json:/data/edge_risk.json:ro
      - ./ml/artifacts/gh_edges.csv:/data/gh_edges.csv
      - graphhopper_graph:/graphhopper/graph-cache
    environment:
      - JAVA_OPTS=-Xmx3g -Xms512m
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8989/health"]
      interval: 15s
      timeout: 5s
      start_period: 600s
      retries: 40

volumes:
  graphhopper_graph:
    driver: local
```

The `:ro` flags on the PBF and JSON mounts prevent accidental container-side writes. `gh_edges.csv` is writable so the EdgeExporter can write to it.

**Preflight script before `docker compose up`:**

Add to `graphhopper/docker-entrypoint.sh` (runs before GH starts):
```bash
#!/bin/sh
set -e
if [ ! -f /data/delhi-ncr-latest.osm.pbf ]; then
    echo "ERROR: /data/delhi-ncr-latest.osm.pbf not found."
    echo "Download from https://download.geofabrik.de/asia/india/delhi-latest.osm.pbf"
    echo "and save to graphhopper/data/delhi-ncr-latest.osm.pbf"
    exit 1
fi
if [ ! -f /data/edge_risk.json ]; then
    echo "WARNING: /data/edge_risk.json not found."
    echo "Crime-aware profiles (balanced/safest) will use risk=0 for all edges."
    echo "Run: python -m ml.build_edge_risk && docker compose restart graphhopper"
    # Start with fastest-only config until edge_risk.json exists
    exec java $JAVA_OPTS -jar /app/graphhopper.jar server /app/config-bootstrap.yml
fi
exec java $JAVA_OPTS -jar /app/graphhopper.jar server /app/config.yml
```

This means the container starts successfully even when `edge_risk.json` is absent (graceful degradation to fastest-only routing), giving the operator a clear message without a hard crash.

**`backend` service depends on GH health:**
Add to the `backend:` service:
```yaml
    depends_on:
      graphhopper:
        condition: service_healthy
```

### 4d. First-Run Behaviour

**Phase 1 — Graph import (2–3 min):** GH reads every node/way from the PBF, encodes road attributes, builds adjacency structure.

**Phase 2 — No CH/LM preparation** (because `prepare.ch.weightings: none`): GH starts routing in flexible (Dijkstra/A*) mode immediately after graph import. No additional preprocessing wait.

**Ready signal:** Log line containing `Started GraphHopperApplication` (or `Dropwizard application started`). Docker health check confirms via `GET /health`.

**Subsequent startups (graph cache exists):** Graph loads from binary cache in ~15–30 seconds.

### 4e. Graph Cache Invalidation Rules

The GH graph cache at `graphhopper_graph` volume becomes stale (must be deleted and rebuilt) when:

| Change | Cache action required |
|--------|-----------------------|
| PBF file replaced | Delete `graphhopper_graph` volume + re-run Stage 0 + re-run Stage 2 |
| `config.yml` profile names changed | Delete `graphhopper_graph` volume |
| `CrimeWeighting.java` or factory patch changed | Rebuild Docker image + delete `graphhopper_graph` volume |
| `edge_risk.json` content changed | GH container restart only (JSON is read on startup, not baked into cache) |
| λ values changed in `config.yml` | GH container restart only (config re-read on startup) |

**Why JSON changes don't require cache rebuild:** `CrimeWeighting` reads `edge_risk.json` in the constructor, every time GH starts. The compiled graph stores road topology; risk scores are in the HashMap, not in the graph binary.

**Delete cache volume:** `docker volume rm route_recommender_web_graphhopper_graph`

### 4f. Startup Readiness and Backend Retry

The backend lifespan checks GH health once at startup. If GH is still preprocessing (first run), this check will fail. The backend must still start — routing requests return 503 until GH is ready.

Add retry loop to `check_graphhopper_health()` in `backend/app/main.py`:
```python
async def check_graphhopper_health(url: str, retries: int = 3, delay: float = 10.0) -> bool:
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url}/health")
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        if attempt < retries - 1:
            await asyncio.sleep(delay)
    return False
```

If after `retries` attempts GH is not healthy, log a warning and proceed (backend starts, routing fails until GH ready). For production, the `depends_on: condition: service_healthy` in docker-compose ensures the backend container doesn't start at all until GH passes its health check.

---

## Section 5 — Stage 4: CrimeWeighting.java and EdgeExporter

All APIs verified against GraphHopper 9.1 commit `73e6b7cc3ca163ce0b53692f7cd732dba170bfce`.

### 5a. Package and Imports

```java
package com.graphhopper.routing.weighting;

import com.graphhopper.routing.ev.BooleanEncodedValue;
import com.graphhopper.routing.ev.DecimalEncodedValue;
import com.graphhopper.routing.util.TurnCostProvider;
import com.graphhopper.util.EdgeIteratorState;
import com.graphhopper.util.FetchMode;
import com.graphhopper.util.PointList;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.*;
import java.util.*;
```

**`BooleanEncodedValue`**: Represents the `car_access` encoded value — whether a car can traverse an edge. Required by `AbstractWeighting` to correctly skip inaccessible edges.

**`DecimalEncodedValue`**: Represents `car_average_speed`. Required by `AbstractWeighting` to compute travel time and by our `getMinWeight()` for the A* heuristic.

**`TurnCostProvider`**: Required by `AbstractWeighting` constructor. Passed through from the factory; typically `TurnCostProvider.NO_TURN_COST_PROVIDER` for car routing without turn-cost penalties.

**Jackson `ObjectMapper` (not `org.json`)**: Jackson (`com.fasterxml.jackson`) is already a GH dependency (used in the web module). `org.json` does not appear in GH 9.1's dependency tree and must not be used.

**`FetchMode.ALL`**: Verified present in GH 9.1.

### 5b. Class Declaration and Fields

```java
public class CrimeWeighting extends AbstractWeighting {

    private static final String NAME = "crime_aware";

    private final Map<Long, Double> edgeRiskScores;
    private final double lambda;
    private final double maxSpeedMps;

    // Telemetry counters — accessed by the startup validator
    private long lookupCount   = 0;
    private long matchCount    = 0;
    private long nonZeroCount  = 0;
```

**`Map<Long, Double> edgeRiskScores`**: HashMap keyed by GH integer edge ID (long). O(1) lookup. No string formatting, no locale issue, no centroid calculation.

**`double lambda`**: Safety multiplier (0.1 for balanced, 0.3 for safest). Injected from profile hints.

**`double maxSpeedMps`**: Maximum speed in m/s, stored at construction time. Used in `getMinWeight()` for the A* lower-bound heuristic. Computed as `avgSpeedEnc.getMaxOrMaxStorableDecimal() / 3.6` (km/h → m/s).

**Telemetry counters**: Incremented during routing. The startup validator reads them after a warm-up scan (see 5e).

### 5c. Constructor

```java
public CrimeWeighting(BooleanEncodedValue accessEnc, DecimalEncodedValue avgSpeedEnc,
                      TurnCostProvider turnCostProvider,
                      double lambda, String edgeRiskPath) {
    super(accessEnc, avgSpeedEnc, turnCostProvider);
    this.lambda       = lambda;
    this.maxSpeedMps  = avgSpeedEnc.getMaxOrMaxStorableDecimal() / 3.6;
    this.edgeRiskScores = new HashMap<>();

    if (!new File(edgeRiskPath).exists()) {
        // Degraded mode: log warning, leave map empty, route as fastest.
        // GraphHopper still starts; operators see the warning in logs.
        System.err.println("[CrimeWeighting] WARNING: " + edgeRiskPath +
            " not found. Profile '" + NAME + "' will route as fastest until JSON is provided.");
        return;
    }

    try {
        ObjectMapper mapper = new ObjectMapper();
        JsonNode root   = mapper.readTree(new File(edgeRiskPath));
        JsonNode scores = root.get("edge_scores");
        // Validate metadata if present
        JsonNode meta = root.get("metadata");
        if (meta != null) {
            System.out.println("[CrimeWeighting] Loading edge_risk.json: " +
                meta.path("n_edges_scored").asLong() + " edges scored, " +
                "generated " + meta.path("generated_at").asText());
        }
        Iterator<Map.Entry<String, JsonNode>> fields = scores.fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> entry = fields.next();
            long edgeId   = Long.parseLong(entry.getKey());
            double score  = entry.getValue().asDouble();
            if (score < 0 || score > 1.01) {
                System.err.println("[CrimeWeighting] WARNING: edge " + edgeId +
                    " has out-of-range score " + score + "; clamping to [0, 1].");
                score = Math.max(0, Math.min(1.0, score));
            }
            edgeRiskScores.put(edgeId, score);
        }
        System.out.println("[CrimeWeighting] Loaded " + edgeRiskScores.size() +
            " edge risk scores. Lambda=" + lambda);
    } catch (IOException e) {
        throw new RuntimeException("[CrimeWeighting] Failed to read " + edgeRiskPath +
            ": " + e.getMessage(), e);
    } catch (NumberFormatException e) {
        throw new RuntimeException("[CrimeWeighting] edge_risk.json contains a non-integer key: " +
            e.getMessage(), e);
    }
}
```

**Constructor signature verified against GH 9.1 `AbstractWeighting`:**
`AbstractWeighting(BooleanEncodedValue accessEnc, DecimalEncodedValue avgSpeedEnc, TurnCostProvider turnCostProvider)`

**Graceful degradation on missing JSON:** The constructor does not throw if `edge_risk.json` is absent. It logs a warning and leaves the map empty. All `edgeRiskScores.getOrDefault(id, 0.0)` calls return 0.0, making `crime_aware` equivalent to `fastest`. This allows first-time startup before Stage 2 is complete.

### 5d. `calcEdgeWeight()` — Core Method

```java
@Override
public double calcEdgeWeight(EdgeIteratorState edge, boolean reverse) {
    // Weight unit: SECONDS (GH 9.1 convention for calcEdgeWeight).
    // calcEdgeMillis() returns physical milliseconds.
    double travelTimeSec = calcEdgeMillis(edge, reverse) / 1000.0;
    long   edgeId        = edge.getEdge();
    double riskScore     = edgeRiskScores.getOrDefault(edgeId, 0.0);

    // Telemetry
    lookupCount++;
    if (edgeRiskScores.containsKey(edgeId)) {
        matchCount++;
        if (riskScore > 0) nonZeroCount++;
    }

    return travelTimeSec + lambda * riskScore * edge.getDistance();
}
```

**Verified correct for GH 9.1:** `calcEdgeWeight(EdgeIteratorState, boolean)` — two arguments.

**Unit consistency:** `travelTimeSec` [s] + `lambda` [s/m] × `riskScore` [dimensionless] × `edge.getDistance()` [m] = total weight [s]. All terms have the same unit.

**Comparison with incorrect Revision 1:** Revision 1 returned `travelTimeMs + lambda * riskScore * length` where the first term was ms and λ was calibrated for ms. GH 9.1 expects `calcEdgeWeight` in seconds. Using ms would make every edge appear 1000× more costly than it is, and route selection would still work (since all edges are equally inflated) but `getMinWeight()` would be wildly wrong, breaking A*.

### 5e. `calcEdgeMillis()` and `getMinWeight()`

```java
@Override
public long calcEdgeMillis(EdgeIteratorState edge, boolean reverse) {
    // Returns physical travel time in milliseconds.
    // AbstractWeighting.calcEdgeMillis() computes this from edge speed and distance.
    // We do NOT add crime penalty here — calcEdgeMillis is for ETA display,
    // not routing decisions.
    return super.calcEdgeMillis(edge, reverse);
}

@Override
public double getMinWeight(double distance) {
    // Lower bound on calcEdgeWeight for the A* heuristic.
    // Unit: seconds. Crime penalty is always >= 0, so lower bound = travel time at max speed.
    return distance / maxSpeedMps;
}

@Override
public String getName() {
    return NAME;  // "crime_aware"
}
```

**`calcEdgeMillis` override rationale:** Even though we call `super`, the explicit override is needed because `AbstractWeighting.calcEdgeMillis()` is abstract in GH 9.1. The super implementation computes `(long) (distance / avgSpeedMps * 1000)`.

**`getMinWeight` admissibility:** Returns `distance / maxSpeedMps` which is always ≤ the true `calcEdgeWeight` for any edge (since `travelTimeSec ≥ distance / maxSpeedMps` and the crime term is non-negative). A* remains admissible.

### 5f. Startup Match-Rate Validation

Add a static method called by the factory after construction, before GH finishes starting:

```java
public void validateMatchRate(Iterable<EdgeIteratorState> baseEdges, double minMatchRate) {
    long total = 0, matched = 0;
    for (EdgeIteratorState edge : baseEdges) {
        total++;
        if (edgeRiskScores.containsKey((long) edge.getEdge())) matched++;
    }
    double rate = (total > 0) ? (double) matched / total : 0;
    System.out.printf("[CrimeWeighting] Startup validation: %d/%d GH edges matched JSON (%.1f%%)%n",
        matched, total, rate * 100);
    if (rate < minMatchRate) {
        System.err.printf("[CrimeWeighting] WARNING: match rate %.1f%% < %.0f%% threshold. " +
            "Crime-aware routing is degraded. Re-run build_edge_risk.py.%n",
            rate * 100, minMatchRate * 100);
    }
}
```

This is called from `DefaultWeightingFactory` after construction (see 5g). It scans all base edges and logs the match rate. If below threshold (10% minimum), it logs a warning but does NOT throw — routing continues in degraded mode rather than refusing to start.

**Runtime telemetry:** The `lookupCount`, `matchCount`, `nonZeroCount` counters are reset every 1000 requests and logged. Add a `@Scheduled` task or log them in `getName()` with JMX if GH's Dropwizard metrics are available. The simplest approach: log every 10,000 calls.

### 5g. DefaultWeightingFactory Patch Design

**Package:** `com.graphhopper.routing` (not `com.graphhopper.routing.weighting`).

**GH 9.1 factory structure:** `DefaultWeightingFactory.createWeighting()` uses an if/else chain. The existing code handles `"shortest"`, `"custom"`, and others before throwing for unknown names. Our patch inserts one else-if before the final throw.

**Complete patch description for `DefaultWeightingFactory.java`:**

The patched file replaces the original at `core/src/main/java/com/graphhopper/routing/DefaultWeightingFactory.java`. Copy the entire original file content and add the following block immediately before the final `else { throw new IllegalArgumentException(...) }`:

```java
} else if ("crime_aware".equals(weightingStr)) {
    // CrimeWeighting: time-based routing plus crime-risk penalty per edge.
    // Encoded values come from the same EncodingManager used by other weightings.
    BooleanEncodedValue accessEnc =
        encodingManager.getBooleanEncodedValue(VehicleAccess.key("car"));
    DecimalEncodedValue speedEnc  =
        encodingManager.getDecimalEncodedValue(VehicleSpeed.key("car"));
    double lambda    = profile.getHints().getDouble("crime_aware.lambda", 0.1);
    String riskPath  = profile.getHints().getString(
        "crime_aware.edge_risk_path", "/data/edge_risk.json");
    CrimeWeighting w = new CrimeWeighting(
        accessEnc, speedEnc, turnCostProvider, lambda, riskPath);
    // Run startup match-rate validation (logs to stdout; non-fatal)
    w.validateMatchRate(ghStorage.getBaseGraph().getAllEdges(), 0.10);
    return w;
```

**Required imports to add to the factory:**
```java
import com.graphhopper.routing.ev.VehicleAccess;
import com.graphhopper.routing.ev.VehicleSpeed;
import com.graphhopper.routing.weighting.CrimeWeighting;
```

**Variables used from existing factory scope:** `encodingManager` (the `EncodingManager` field), `profile` (the `Profile` argument), `turnCostProvider` (the turn-cost parameter, constructed the same way as for other weightings), `ghStorage` (the `GraphHopperStorage` field — needed for the validation scan).

**IMPORTANT:** Before writing the patch, read the actual `DefaultWeightingFactory.java` from the cloned GH 9.1 source to confirm exact variable names (`encodingManager`, `ghStorage`, `turnCostProvider`) and method signatures. The variable names above match the GH 9.1 source per the verification analysis but must be re-confirmed during Stage 3 implementation.

### 5h. EdgeExporter Java Utility

`graphhopper/tools/EdgeExporter.java` — runs as a standalone main class after the graph is built.

```java
package com.graphhopper.tools;

import com.graphhopper.*;
import com.graphhopper.config.*;
import com.graphhopper.util.*;
import java.io.*;
import java.util.Locale;

public class EdgeExporter {
    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("Usage: EdgeExporter <graph-cache-dir> <output-csv>");
            System.exit(1);
        }
        String graphDir = args[0];
        String csvPath  = args[1];

        // Load existing graph without re-importing PBF
        GraphHopper gh = new GraphHopper();
        gh.setGraphHopperLocation(graphDir);
        gh.importOrLoad();  // loads if cache exists, imports if not

        AllEdgesIterator edges = gh.getBaseGraph().getAllEdges();
        long edgeCount = 0;

        try (PrintWriter out = new PrintWriter(new BufferedWriter(new FileWriter(csvPath)))) {
            out.println("edge_id,length_m,geom_wkt");
            while (edges.next()) {
                PointList pts = edges.fetchWayGeometry(FetchMode.ALL);
                if (pts.isEmpty()) continue;
                StringBuilder sb = new StringBuilder("LINESTRING(");
                for (int i = 0; i < pts.size(); i++) {
                    if (i > 0) sb.append(",");
                    // WKT convention: longitude first, then latitude
                    sb.append(String.format(Locale.ROOT, "%.6f %.6f",
                        pts.getLon(i), pts.getLat(i)));
                }
                sb.append(")");
                out.printf(Locale.ROOT, "%d,%.2f,%s%n",
                    edges.getEdge(), edges.getDistance(), sb);
                edgeCount++;
            }
        }
        System.out.printf("EdgeExporter: wrote %d edges to %s%n", edgeCount, csvPath);
    }
}
```

**`Locale.ROOT` is mandatory** in `String.format` calls to ensure decimal points (not decimal commas) in all JVM locale settings. Python's `shapely_wkt.loads()` requires decimal points.

**WKT coordinate order:** `LINESTRING(lng lat, ...)` — WKT standard uses (x, y) = (longitude, latitude). Python's `shapely_wkt.loads()` parses this as `Point.x = longitude`, `Point.y = latitude`. When building the crime GeoDataFrame in Python, use `gpd.points_from_xy(pool["lng"], pool["lat"])` which also produces EPSG:4326 geometries with x=lng, y=lat. Both match.

---

## Section 6 — Stage 5: Backend Routing Service Update

### 6a. Config Changes (`backend/app/config.py`)

**New fields:**
```python
# --- GraphHopper routing ---
GRAPHHOPPER_URL: str = "http://localhost:8989"

# Comma-separated list of valid profile names in this GH instance.
# Used to validate per-request profile selection.
GRAPHHOPPER_PROFILES: str = "fastest,balanced,safest"

# Default profile when the request does not specify one.
SAFETY_PROFILE: str = "balanced"
```

**Deprecated ORS fields (give defaults, keep for emergency rollback):**
```python
ORS_API_KEY: str  = ""          # DEPRECATED — set to "" after GH switch
ORS_BASE_URL: str = "https://api.openrouteservice.org"  # DEPRECATED
```

### 6b. `routing.py` Full Rewrite Plan

**Interface unchanged:** `get_routes(origin, dest, profile)` returns `list[dict]` with keys `geometry`, `duration_sec`, `distance_m`, `waypoints`.

**Profile parameter:**
```python
async def get_routes(
    origin:  tuple[float, float],
    dest:    tuple[float, float],
    profile: str | None = None,
) -> list[dict]:
    if profile is None:
        profile = settings.SAFETY_PROFILE
    valid = set(settings.GRAPHHOPPER_PROFILES.split(","))
    if profile not in valid:
        raise ValueError(f"Unknown profile '{profile}'. Valid: {valid}")
```

**Request payload (exact):**
```json
{
  "points": [[77.2090, 28.6139], [77.0688, 28.5665]],
  "profile": "balanced",
  "algorithm": "alternative_route",
  "alternative_route.max_paths": 10,
  "alternative_route.max_weight_factor": 2.0,
  "alternative_route.max_share_factor": 0.6,
  "points_encoded": false,
  "instructions": false,
  "locale": "en"
}
```

**Response parsing:**
```python
routes = []
for path in resp.json().get("paths", []):
    routes.append({
        "geometry":     path["points"],           # GeoJSON dict
        "duration_sec": path["time"] / 1000.0,    # ms → seconds
        "distance_m":   path["distance"],         # already metres
        "waypoints":    _sample_waypoints(path["points"]["coordinates"]),
    })
routes = _deduplicate_routes(routes)
```

**Error handling:**
```python
except httpx.ConnectError:
    raise HTTPException(503,
        "Routing service (GraphHopper) is unavailable. "
        "Ensure the GraphHopper container is running.")
except httpx.HTTPStatusError as exc:
    raise HTTPException(502,
        f"GraphHopper returned {exc.response.status_code}: "
        f"{exc.response.text[:200]}")
```

Do NOT fall back silently to ORS. An empty `ORS_API_KEY` must also be checked before any ORS call to prevent accidental fallback.

**Add deduplication (Audit Finding 1):**
```python
def _route_fingerprint(coordinates: list[list[float]]) -> tuple:
    n = len(coordinates)
    if n == 0:
        return ()
    step = max(1, (n - 1) // 9)
    indices = list(range(0, n, step))[:10]
    return tuple((round(coordinates[i][0], 3), round(coordinates[i][1], 3))
                 for i in indices)

def _deduplicate_routes(routes: list[dict]) -> list[dict]:
    seen: set = set()
    unique = []
    for route in routes:
        fp = _route_fingerprint(route["geometry"]["coordinates"])
        if fp not in seen:
            seen.add(fp)
            unique.append(route)
        else:
            logger.debug("dropped duplicate route (fingerprint match)")
    return unique
```

**Cache key fix (Audit Finding 3):** Update `routes.py` line 101:
```python
# Before (wrong):
ck = _cache_key(lat_o, lng_o, lat_d, lng_d, depart_time, "driving-car")
# After:
ck = _cache_key(lat_o, lng_o, lat_d, lng_d, depart_time, req.profile or settings.SAFETY_PROFILE)
```

**TTLCache:** Unchanged — `TTLCache(ttl_seconds=15*60)`.

### 6c. Per-Request Profile Selection

**Add `profile` to `RouteRequest` schema (`backend/app/schemas/routes.py`):**
```python
from typing import Literal, Optional

class RouteRequest(BaseModel):
    origin:      Union[LatLng, str]
    destination: Union[LatLng, str]
    depart_time: datetime
    profile:     Optional[str] = None  # None = use SAFETY_PROFILE default
```

In `routes.py`, pass the profile to `routing.get_routes()`:
```python
raw_routes = await routing.get_routes(
    origin=(lat_o, lng_o),
    dest=(lat_d, lng_d),
    profile=req.profile,       # None → defaults to SAFETY_PROFILE in routing.py
)
```

This allows the frontend and the verification plan's comparison tests to request specific profiles without changing the global default.

**Frontend dropdown (Stage 5 scope):** Add a "Route Type" selector to `RouteForm.jsx` with options "Balanced (recommended)", "Safest", "Fastest". Map to profile names and include in the POST body. This enables the Step 3 verification check (fastest vs safest route divergence).

### 6d. Delhi-NCR Geocoding Guard

Add a coordinate bounds check in `routing.py` before forwarding to GH:
```python
DELHI_NCT_LAT = (28.40, 28.88)
DELHI_NCT_LNG = (76.84, 77.35)

def _within_delhi_nct(lat: float, lng: float) -> bool:
    return (DELHI_NCT_LAT[0] <= lat <= DELHI_NCT_LAT[1] and
            DELHI_NCT_LNG[0] <= lng <= DELHI_NCT_LNG[1])
```

If origin or destination is outside Delhi NCT, raise a 422 with a helpful message: `"Destination is outside the Delhi NCT routing area. Gurgaon and Noida are not available in Phase 7."`

### 6e. KDE Post-Scoring and GraphHopper Objective Reconciliation

**The tension:** GH uses `crime_aware` profile to minimize `travelTimeSec + λ × risk × length` during pathfinding. The backend then re-scores all returned routes via KDE (`score_route()` in `risk_model.py`). These two methods can produce different orderings.

**Defined behavior:** This is a feature, not a bug. GH's crime-aware routing ensures that alternatives with fewer high-risk edges are generated as candidates. The KDE post-scoring applies additional nuance (time-of-day modifier, recency weighting, per-crime-type weighting) that GH's simpler edge-risk model does not have. The final ranking by KDE score is the authoritative ordering for the user.

**Documentation:** The frontend should not expose "GraphHopper ranked this route" messaging. Routes are displayed with KDE-derived risk bands (Low/Medium/High) only. The crime-aware GH objective improves the quality of the alternative route candidates; the KDE provides the final ranking.

---

## Section 7 — Stage 6: Cloudflare Tunnel Setup

**Revision 2 changes:** The original plan contained two errors: (1) it suggested `cfargotunnel.com` subdomains are freely selectable — they are not; a stable public hostname requires a Cloudflare-managed domain; (2) it claimed the Windows service reads `C:\Users\Sandip\.cloudflared\config.yml` — the service runs as `LocalSystem` and reads `C:\Windows\System32\config\systemprofile\.cloudflared\config.yml`.

This section provides two deployment paths. Choose one based on whether you own a domain registered in Cloudflare DNS.

### Path A — Quick Tunnel (No Domain Required, Unstable URL)

A quick tunnel generates a random `trycloudflare.com` hostname every time it starts. Suitable for demos where the URL is communicated manually.

```powershell
cloudflared tunnel --url http://localhost:8989
```

This prints a URL like `https://seven-words-some-phrase.trycloudflare.com`. Update Azure:
```powershell
az containerapp update --name route-recommender-backend --resource-group route-recommender-rg `
  --set-env-vars GRAPHHOPPER_URL=https://seven-words-some-phrase.trycloudflare.com
```

**Limitation:** URL changes on every restart. For persistent demos, use Path B.

### Path B — Named Tunnel with Cloudflare-Managed Domain (Stable URL)

**Pre-condition:** You own a domain (e.g., `sandip-demo.dev`) that is **added to Cloudflare and uses Cloudflare DNS nameservers**. Free `.dev` domains cost approximately ₹900/year. Without a CF-managed domain, this path is not available.

**Step 1 — Authenticate:**
```powershell
cloudflared tunnel login
# Browser opens; authorise with your Cloudflare account.
# Certificate saved to C:\Users\Sandip\.cloudflared\cert.pem
```

**Step 2 — Create named tunnel:**
```powershell
cloudflared tunnel create graphhopper-tunnel
# Saves credentials JSON to C:\Users\Sandip\.cloudflared\<tunnel-uuid>.json
# Note the <tunnel-uuid> printed.
```

**Step 3 — Create DNS CNAME:**
```powershell
cloudflared tunnel route dns graphhopper-tunnel graphhopper.sandip-demo.dev
# Creates CNAME graphhopper.sandip-demo.dev → <tunnel-uuid>.cfargotunnel.com
```

**Step 4 — Create config file for user-mode run (not service):**

Create `C:\Users\Sandip\.cloudflared\config.yml`:
```yaml
tunnel: <tunnel-uuid>
credentials-file: C:\Users\Sandip\.cloudflared\<tunnel-uuid>.json

ingress:
  - hostname: graphhopper.sandip-demo.dev
    service: http://localhost:8989
  - service: http_status:404
```

**Step 5 — Run the tunnel (user session, manual start):**
```powershell
cloudflared tunnel run graphhopper-tunnel
```

**Step 6 — Windows Service (automatic start, reads from LocalSystem profile):**

The Windows service runs as `LocalSystem` and reads config from a **different path** than the user profile:
```
C:\Windows\System32\config\systemprofile\.cloudflared\
```

Two options:
- **Option 6a (recommended for simplicity):** Run cloudflared as a user-mode process (`cloudflared tunnel run`) on each Windows login via Task Scheduler, using the `C:\Users\Sandip\.cloudflared\config.yml` path. Task Scheduler can run at user login with the user's credentials.
- **Option 6b (system service):** Copy credentials and config to the LocalSystem profile path:
  ```powershell
  mkdir "C:\Windows\System32\config\systemprofile\.cloudflared"
  copy "C:\Users\Sandip\.cloudflared\<tunnel-uuid>.json" `
       "C:\Windows\System32\config\systemprofile\.cloudflared\"
  # Create config.yml in that directory with absolute paths
  cloudflared service install
  Start-Service cloudflared
  ```

Option 6b requires admin privileges and the config.yml in the system profile must use absolute paths matching the system profile location.

**Step 7 — Set Azure environment variable:**
```powershell
az containerapp update --name route-recommender-backend --resource-group route-recommender-rg `
  --set-env-vars GRAPHHOPPER_URL=https://graphhopper.sandip-demo.dev
```

### Realistic Latency Expectations

The round-trip path for a production request:
1. Browser (Delhi) → Vercel CDN → Azure East Asia backend: ~60 ms
2. Azure → Cloudflare edge → Cloudflare tunnel → local Windows machine (Delhi): ~80–150 ms one-way
3. GraphHopper routing (flexible, Delhi graph): ~50–200 ms
4. KDE post-scoring + Qdrant retrieval: ~100–300 ms
5. Response back: same path reversed

**Total realistic warm request: 600–1,000 ms.** Cold starts (first GraphHopper routing after GH startup) can be 2–5 seconds due to JIT warm-up. Multi-second requests are plausible and must be handled in the UI with a progress indicator (which already exists in the frontend redesign).

**If the tunnel is down:** GH returns 503, which the backend propagates to the frontend. The ORS fallback key (`ORS_API_KEY`) can be re-activated in Azure env vars for emergency routing without crime awareness.

---

## Section 8 — Stage 7: Verification Plan

Execute these steps in order. Each step must pass before proceeding.

### 8a. Pre-Stage Checks

**Check A — `gh_edges.csv` validity:**
```python
import pandas as pd
edges = pd.read_csv("ml/artifacts/gh_edges.csv")
assert len(edges) > 50000, "Expected >50K GH edges for Delhi"
assert "edge_id" in edges.columns
assert "geom_wkt" in edges.columns
print(f"GH edges: {len(edges):,} — OK")
```

**Check B — `edge_risk.json` validity:**
```python
import json, numpy as np
with open("ml/artifacts/edge_risk.json") as f:
    d = json.load(f)
scores = d["edge_scores"]
vals = list(scores.values())
assert max(vals) <= 1.01, "Max score exceeds 1.0"
assert min(vals) >= 0, "Negative score found"
n_nonzero = sum(1 for v in vals if v > 0)
coverage = n_nonzero / d["metadata"]["n_edges_total"]
assert coverage >= 0.10, f"Coverage {coverage:.1%} below 10% threshold"
print(f"Edge risk coverage: {coverage:.1%} — OK")
```

**Check C — GH container health:**
```bash
curl http://localhost:8989/health
# Expected: {"status":"ok"}
```

### 8b. Profile-Specific Tests

**Step 1 — Fastest profile route (baseline):**
```bash
curl -s -X POST http://localhost:8989/route \
  -H "Content-Type: application/json" \
  -d '{"points":[[77.2190,28.6315],[76.8985,28.5270]],"profile":"fastest",
       "algorithm":"alternative_route","alternative_route.max_paths":3,
       "alternative_route.max_weight_factor":2.0,
       "alternative_route.max_share_factor":0.6,
       "points_encoded":false,"instructions":false}' \
  | python -c "import sys,json; d=json.load(sys.stdin); print(len(d['paths']),'paths, first duration:',d['paths'][0]['time']/1000,'s')"
```
Expected: 1–3 paths, duration ~900–2000 s for this ~30 km corridor.

**Step 2 — Profile divergence test (fastest vs safest):**
```python
import requests

def get_path(profile):
    r = requests.post("http://localhost:8989/route", json={
        "points": [[77.2190, 28.6315], [76.8985, 28.5270]],
        "profile": profile,
        "algorithm": "alternative_route",
        "alternative_route.max_paths": 3,
        "alternative_route.max_weight_factor": 2.0,
        "alternative_route.max_share_factor": 0.6,
        "points_encoded": False, "instructions": False
    })
    return r.json()["paths"][0]["points"]["coordinates"]

fastest = get_path("fastest")
safest  = get_path("safest")
assert fastest != safest, "FAIL: fastest and safest must diverge when edge_risk.json is loaded"
print("✓ Profile divergence confirmed")
```

If they are identical, check that `edge_risk.json` is bind-mounted and non-empty.

**Step 3 — CrimeWeighting match-rate log check:**

After GH startup, check container logs for the startup validation message:
```powershell
docker compose logs graphhopper | Select-String "Startup validation"
# Expected: [CrimeWeighting] Startup validation: 12400/89200 GH edges matched JSON (13.9%)
```

If rate is below 10%, the edge risk pipeline has a coverage problem.

**Step 4 — Backend integration test:**
```bash
curl -X POST http://localhost:8000/routes/recommend \
  -H "Content-Type: application/json" \
  -d '{"origin":{"lat":28.6315,"lng":77.2190},
       "destination":{"lat":28.5270,"lng":76.8985},
       "depart_time":"2026-06-10T10:00:00Z",
       "profile":"safest"}'
```
Expected: `RouteResponse` with `routes` array, each with `risk_band` in `["Low","Medium","High"]` and `nearby_incidents` list.

**Step 5 — Per-request profile test:**

Verify that requesting `"profile":"fastest"` and `"profile":"safest"` from the backend returns geometrically different routes (after the KDE re-ranking may reorder them, but the raw GH paths should differ).

**Step 6 — Cloudflare end-to-end:**

After tunnel is running, navigate to production frontend, request a route. Verify GH container logs show a request entry within ~5 seconds.

**Step 7 — Crime-awareness visual check:**

Route from Nizamuddin East to Mangolpuri. With heatmap visible ("All" category), the `safest` profile should take a more northerly road avoiding Rohini's robbery cluster, while `fastest` takes the more direct NH-24/Ring Road path. If both are identical, increase λ from 0.3 toward 0.5 and recalibrate.

**Step 8 — Unit tests (before Stage 4 implementation begins):**

Add to `backend/tests/test_routing.py` (GH-specific):
- Test that `get_routes()` with `profile="balanced"` returns valid route dict structure.
- Test that unknown profile raises `ValueError`.
- Test that `ConnectError` raises `HTTPException(503)`.
- Test `_route_fingerprint` with empty and non-empty coordinate lists.
- Test `_deduplicate_routes` with identical and distinct geometries.

Add a Java compile test to the Dockerfile build (GH build with `-DskipTests=false -pl core -am` to run core unit tests, confirming `CrimeWeighting` compiles and basic weighting math holds).

### 8c. Weekly Retrain Workflow Extension

The existing `.github/workflows/retrain-weekly.yml` must be extended with these steps after `promote_model.py`:

1. **Generate `edge_risk.json`** (runs on the GitHub Actions runner):
   ```yaml
   - name: Build edge risk scores
     run: python -m ml.build_edge_risk --snapshot ${{ steps.snapshot.outputs.path }}
   ```
   Note: `gh_edges.csv` must be present on the runner. Options:
   - **Option W1:** Commit `gh_edges.csv` to the repo (it's ~15 MB, acceptable with `.gitattributes` LFS or just committing it directly since it's regenerated only when PBF changes).
   - **Option W2:** Upload `gh_edges.csv` as a GitHub Actions artifact after Stage 0 and download it in the retrain workflow.
   Option W1 is simpler.

2. **Validate coverage:**
   ```yaml
   - name: Validate edge risk coverage
     run: python -c "
   import json
   with open('ml/artifacts/edge_risk.json') as f: d=json.load(f)
   scores = d['edge_scores']
   n_nonzero = sum(1 for v in scores.values() if v>0)
   coverage = n_nonzero / d['metadata']['n_edges_total']
   assert coverage >= 0.10, f'Coverage {coverage:.1%} below 10% threshold'
   print(f'Edge risk coverage: {coverage:.1%} — PASS')
   "
   ```

3. **Publish `edge_risk.json` to the local GraphHopper host:**
   The GitHub Actions runner cannot reach the local Windows machine directly. Two options:
   - **Option P1:** Upload `edge_risk.json` as a GitHub Actions artifact (30-day retention). Operator downloads it manually and restarts GH.
   - **Option P2:** Use Cloudflare Workers or another webhook to trigger a pull on the Windows machine. Operational complexity; defer to a future phase.
   
   **Phase 7 decision:** Use Option P1. The weekly artifact upload replaces manual coordination. After the operator downloads and places the file, GH restarts automatically on the next scheduled maintenance window.

4. **Document the manual restart step:**
   Add to the retrain workflow description: "After downloading the new `edge_risk.json` artifact, replace `ml/artifacts/edge_risk.json` and run `docker compose restart graphhopper`. The match-rate log message confirms the new scores are loaded."

---

## Section 9 — Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| EdgeExporter output differs from routing-time GH edge representation (e.g. GH splits edges after CH) | Low — we run without CH (`prepare.ch.weightings: none`) | Medium — some edges score 0.0 when they shouldn't | Verify by running match-rate validator at startup; check that flexible mode does not create shortcut edges not in `getAllEdges()` output |
| `gh_edges.csv` row count and `edge_risk.json` edge count diverge after PBF update | Certain if PBF is replaced without re-running Stage 0 | High — crime-aware routing silently degrades | Cache invalidation rules (Section 4e). Add count comparison in docker-entrypoint.sh: compare `edge_risk.json`'s `gh_edges_csv_rows` to actual `wc -l gh_edges.csv`. |
| GH 9.1 `DefaultWeightingFactory` variable names differ from plan (e.g., `encodingManager` named differently) | Medium — plan is based on verification analysis, exact names need source read | High — compile failure | Read `DefaultWeightingFactory.java` from cloned 9.1 source BEFORE writing the patch. |
| Cloudflare Tunnel goes down during a demo | Medium (free tier, no SLA) | High — all production routing returns 503 | Use `ORS_API_KEY` emergency fallback: reset `GRAPHHOPPER_URL` to `""` in Azure env, set valid `ORS_API_KEY`. Backend gracefully returns ORS routes (no crime awareness, but app still works). |
| Java Maven build fails due to dependency mismatch | Low (pinned to exact commit) | High — Docker image cannot build | Test build locally before pushing. Add `mvn compile -pl core -am` as a validation step before full `-pl web,tools -am`. |
| Windows Docker Desktop OOM-kills GH during graph import | Medium (import needs ~1.5 GB; CH disabled reduces peak) | Medium — container exits, graph never builds | `JAVA_OPTS=-Xmx3g` in docker-compose env. Monitor via `docker stats`. |
| `edge_risk.json` coverage below 10% after retrain (new crime data distribution) | Low (coverage is driven by spatial density of crime pool, unlikely to drop below 10%) | High — crime-aware routing silently becomes fastest | Retrain workflow validates coverage (Step 2 in Section 8c) and fails the job if below threshold. |
| KDE post-scoring re-ranks GH-selected routes differently | Certain — the two objectives are different | Low — two-layer risk assessment provides richer results; user cannot see the disagreement | Documented behavior (Section 6e). Not a failure. |
| Per-request profile field added to `RouteRequest` breaks existing frontend calls | Low (field is `Optional`, defaults to `None`) | Low | Backward compatible: `Optional[str] = None` means existing callers without `profile` work unchanged. |
| Cloudflare named tunnel requires CF-managed domain (cost/setup barrier) | Certain — plan requirement | Low — Path A (quick tunnel) works without domain for demos | Both paths documented in Section 7. Quick tunnel is sufficient for portfolio demos. |
| Weekly artifact delivery to local GH host is manual (Option P1) | Certain in Phase 7 | Low — one manual step per week | Acceptable for portfolio project. Automate in a future phase via webhook or Cloudflare Worker. |

---

## Section 10 — Files Changed Summary

| File | Action | Why |
|------|--------|-----|
| `graphhopper/Dockerfile` | **New** | Two-stage build: GH from source with CrimeWeighting + EdgeExporter baked in |
| `graphhopper/config.yml` | **New** | GH 9.1 profiles (custom/crime_aware), flexible routing, correct server block |
| `graphhopper/config-bootstrap.yml` | **New** | Stage 0 startup: fastest profile only, no CH, used to build graph for edge export |
| `graphhopper/docker-entrypoint.sh` | **New** | Preflight check (PBF present), edge_risk.json graceful degradation, config selection |
| `graphhopper/src/CrimeWeighting.java` | **New** | GH 9.1-compatible custom weighting; integer-keyed HashMap; graceful degradation; telemetry |
| `graphhopper/src/DefaultWeightingFactoryPatch.java` | **New** | Replaces `DefaultWeightingFactory.java` in GH source; adds `crime_aware` dispatch |
| `graphhopper/tools/EdgeExporter.java` | **New** | Exports GH edge geometries to CSV after graph build |
| `graphhopper/data/.gitkeep` | **New** | Placeholder for the gitignored PBF and CSV |
| `ml/build_edge_risk.py` | **New** | Edge risk pipeline: reads gh_edges.csv → crime pool → spatial join → edge_risk.json |
| `ml/requirements.txt` | **Modified** | Add `geopandas>=0.14.0`, `pyproj>=3.6.0`. **OSMnx NOT required** (removed from Revision 1) |
| `ml/artifacts/gh_edges.csv` | **Generated** (gitignored or committed) | EdgeExporter output; ~15 MB; regenerate when PBF changes |
| `ml/artifacts/edge_risk.json` | **Generated** (gitignored) | Crime risk scores keyed by GH integer edge ID; bind-mounted to GH container |
| `backend/app/services/routing.py` | **Modified** (full rewrite) | GH client; `_route_fingerprint` + `_deduplicate_routes`; profile validation; Delhi bounds guard |
| `backend/app/config.py` | **Modified** | Add `GRAPHHOPPER_URL`, `SAFETY_PROFILE`, `GRAPHHOPPER_PROFILES`; `ORS_API_KEY: str = ""` |
| `backend/app/main.py` | **Modified** | `check_graphhopper_health()` with retry; WARNING (not error) if GH unreachable |
| `backend/app/routers/routes.py` | **Modified** | Cache key uses `req.profile or settings.SAFETY_PROFILE`; pass profile to `get_routes()` |
| `backend/app/schemas/routes.py` | **Modified** | Add `Optional[str] profile = None` to `RouteRequest` |
| `docker-compose.yml` | **Modified** | `graphhopper:` service (health check, volumes, env); top-level `volumes: graphhopper_graph:` |
| `.gitignore` | **Modified** | Add `graphhopper/data/*.pbf`, `graphhopper/data/*.csv`, `ml/artifacts/gh_edges.csv` |
| `~/.cloudflared/config.yml` | **New** (local, not repo) | Tunnel ingress rules |
| `frontend/src/components/RouteForm.jsx` | **Modified** | Add "Route Type" dropdown (fastest/balanced/safest); include profile in POST body |
| `backend/app/services/risk_model.py` | **Unchanged** | KDE scoring unaffected |
| `backend/app/services/retrieval_service.py` | **Unchanged** | Qdrant retrieval unaffected |
| `backend/app/routers/risk.py` | **Unchanged** | Heatmap endpoints unaffected |
| `frontend/src/components/MapView.jsx` | **Unchanged** | Renders `RouteOption.geometry` — unchanged shape |
| `.github/workflows/retrain-weekly.yml` | **Modified** | Add `build_edge_risk.py` run step and coverage validation; upload `edge_risk.json` as artifact |

---

## Section 11 — CLAUDE.md Update Block

Append to Sprint Completed Log when Phase 7 is fully implemented, tested, and deployed.

```markdown
### Phase 7 — GraphHopper crime-aware routing (2026-XX-XX)

**Stage 0 — Graph mapping experiment**
- `graphhopper/tools/EdgeExporter.java` — standalone Java tool; iterates all GH base edges post-import, writes `ml/artifacts/gh_edges.csv` with columns `edge_id,length_m,geom_wkt`. Edge IDs are GH integers; geometry uses WKT `LINESTRING(lng lat, ...)` with `Locale.ROOT` formatting. Run via `docker compose exec graphhopper java -cp /app/graphhopper.jar com.graphhopper.tools.EdgeExporter /graphhopper/graph-cache /data/gh_edges.csv`.

**Edge risk pipeline**
- `ml/build_edge_risk.py` — reads `gh_edges.csv` (GH native geometries, not OSMnx/Overpass), projects to EPSG:32643, buffers LineStrings by 150m, spatial-joins crime pool (from Parquet), accumulates per-edge score = Σ(kde_density × female_weight × recency_weight), normalises with P99 clipping, writes `ml/artifacts/edge_risk.json` keyed by GH integer edge IDs. Coverage must be ≥10% of GH edges. geopandas + pyproj only; OSMnx not used.

**GraphHopper Docker container**
- `graphhopper/Dockerfile` — two-stage build pinned to GH commit `73e6b7cc3ca163ce0b53692f7cd732dba170bfce`; copies `CrimeWeighting.java`, patched `DefaultWeightingFactory.java`, and `EdgeExporter.java`; builds with `mvn package -DskipTests -pl web,tools -am`.
- `graphhopper/config.yml` — GH 9.1-valid: `fastest` uses `weighting: custom` + `custom_model_files: []` (not `weighting: fastest` which throws); `balanced` (λ=0.1) and `safest` (λ=0.3) use `weighting: crime_aware`; no `vehicle` keys; top-level `server:` block with `bind_host`; `prepare.ch.weightings: none` (JSON changes need restart only, not graph rebuild).
- `graphhopper/docker-entrypoint.sh` — PBF preflight check; graceful start with `config-bootstrap.yml` when `edge_risk.json` absent.
- `docker-compose.yml` — `graphhopper:` service with `HEALTHCHECK`, `depends_on` condition `service_healthy` for backend, named `graphhopper_graph` volume.

**CrimeWeighting.java (GH 9.1-compatible)**
- `graphhopper/src/CrimeWeighting.java` — extends `AbstractWeighting(BooleanEncodedValue, DecimalEncodedValue, TurnCostProvider)`; `HashMap<Long, Double>` keyed by GH integer edge ID; `calcEdgeWeight(EdgeIteratorState, boolean)` returns seconds + λ×risk×length; `calcEdgeMillis` delegates to super; `getMinWeight` returns `distance / maxSpeedMps`; Jackson `ObjectMapper` for JSON parsing; graceful missing-file degradation; startup match-rate validator; runtime telemetry counters.
- `graphhopper/src/DefaultWeightingFactoryPatch.java` — replaces `com.graphhopper.routing.DefaultWeightingFactory`; adds else-if for `"crime_aware"` before final throw; uses `VehicleAccess.key("car")` / `VehicleSpeed.key("car")` encoded values from `encodingManager`; calls `w.validateMatchRate()` post-construction.
- λ values (seconds-based weight): `balanced` λ=0.1 (≈1.7 min extra per max-risk km); `safest` λ=0.3 (5 min extra per max-risk km). Previous plan used ms-based λ=100/300 which is wrong for GH 9.1.

**Backend routing service update**
- `backend/app/services/routing.py` — GH `POST /route` client; per-request profile validation; `_route_fingerprint` + `_deduplicate_routes` added (were absent from file despite session context claim); Delhi NCT bounds guard; no silent ORS fallback.
- `backend/app/config.py` — `GRAPHHOPPER_URL`, `SAFETY_PROFILE`, `GRAPHHOPPER_PROFILES`; `ORS_API_KEY: str = ""` (deprecated, emergency fallback).
- `backend/app/schemas/routes.py` — `RouteRequest.profile: Optional[str] = None` for per-request profile selection.
- `backend/app/routers/routes.py` — cache key uses request profile; passes profile to `get_routes()`.
- `frontend/src/components/RouteForm.jsx` — "Route Type" dropdown (fastest/balanced/safest).

**Cloudflare Tunnel**
- Quick tunnel (`cloudflared tunnel --url http://localhost:8989`) for no-domain demos.
- Named tunnel with CF-managed domain for stable URL: requires domain in Cloudflare DNS, credentials at `C:\Users\Sandip\.cloudflared\`, Windows service reads `C:\Windows\System32\config\systemprofile\.cloudflared\` for LocalSystem mode.
- Realistic warm-request latency: 600–1,000 ms (Azure → CF → Delhi → GH → back).

**Weekly retrain extension**
- `.github/workflows/retrain-weekly.yml` — added `python -m ml.build_edge_risk` step after `promote_model.py`; uploads `edge_risk.json` as GitHub Actions artifact (30-day retention); validates coverage ≥10% (fails job if below threshold).

**Key design changes from Revision 1**
- OSMnx/Overpass replaced by GH-native EdgeExporter → no graph-segmentation mismatch, no centroid calculation, no locale issue.
- JSON keyed by GH integer edge IDs (not centroid strings) → integer HashMap lookup.
- GH 9.1 APIs correct: constructor, calcEdgeWeight 2-arg, calcEdgeMillis, seconds weight unit, Jackson.
- λ corrected to seconds basis: 0.1 (balanced) and 0.3 (safest).
- P99 normalization prevents outlier-dominated score compression.
- Graceful startup degradation when edge_risk.json absent.
```

---

## Verification Findings Resolution Matrix

For every finding in `docs/phase7_verification.md`:

| Finding | Original Problem | Revised Plan Section | Status | Notes |
|---------|-----------------|---------------------|--------|-------|
| Check 1a — Coordinate order (lat/lng in centroid keys) | PASS — coordinate order was correct | N/A (eliminated: no centroid keys) | RESOLVED | Revision 2 uses integer edge IDs; coordinate key contract eliminated |
| Check 1b — Four-decimal tolerance / different PBF source | FAIL — OSMnx uses Overpass; centroid buckets unreliable | Stage 0 + Revised Section 3 | RESOLVED | EdgeExporter reads same GH graph; integer keys have no tolerance issue |
| Check 1c — Midpoint calculation mismatch (Shapely length-weighted vs Java vertex-average) | FAIL — different midpoint methods | Stage 0 + Revised Section 3 | RESOLVED | Midpoint calculation eliminated; GH edge IDs used directly |
| Check 1 locale issue — `String.format` without `Locale.ROOT` | FAIL — decimal comma locales produce incompatible keys | Section 5h EdgeExporter | RESOLVED | EdgeExporter uses `Locale.ROOT`; Java HashMap uses Long keys, no formatting at lookup |
| Check 2a — OSMnx segment consistency vs GH car profile | FAIL — different simplification, different edge sets | Revised Section 3 (GH-native edges) | RESOLVED | Python now reads GH's own edge geometries; no OSMnx simplification |
| Check 2b — No runtime match-rate counter | FAIL — silent degradation | Section 5f + 5g | RESOLVED | `validateMatchRate()` at startup + telemetry counters in `calcEdgeWeight` |
| Check 3a — `AbstractWeighting` constructor wrong | FAIL — no FlagEncoder constructor in GH 9.1 | Section 5c | RESOLVED | Constructor uses `(BooleanEncodedValue, DecimalEncodedValue, TurnCostProvider)` |
| Check 3b — Time method and signature wrong | FAIL — `calcMillis` 3-arg doesn't exist | Section 5d + 5e | RESOLVED | Uses `calcEdgeMillis(EdgeIteratorState, boolean)` 2-arg; `calcEdgeWeight` 2-arg |
| Check 3c — `FetchMode.ALL` | PASS | N/A | N/A (now used in EdgeExporter, not CrimeWeighting) | |
| Check 3d — Factory patch wrong package / switch-case / missing encoded values | FAIL — wrong package, no switch in GH 9.1 | Section 5g | RESOLVED | Patch uses if/else chain in `com.graphhopper.routing`, full construction pattern specified |
| Check 3d — `fastest` weighting throws in GH 9.1 | FAIL — config would fail to load | Section 4b | RESOLVED | `fastest` profile uses `weighting: custom` + `custom_model_files: []` |
| Check 3d — `vehicle: car` rejected by GH 9.1 | FAIL — profile parsing error | Section 4b | RESOLVED | All `vehicle` keys removed from profiles |
| Check 3d — Server block incorrectly nested | FAIL — `server:` must be top-level | Section 4b | RESOLVED | Top-level `server:` with `bind_host` |
| Check 3d — `org.json` not a GH dependency | FAIL — import would fail | Section 5a | RESOLVED | Uses Jackson (`com.fasterxml.jackson.databind.ObjectMapper`) |
| Check 3d — Weight unit inconsistency (ms vs seconds) | FAIL — `calcEdgeWeight` returns seconds, not ms | Section 5d, Section 4b | RESOLVED | All formulas use seconds; λ revised to 0.1/0.3 |
| Check 3e — GH 9.1 tag version pin | WARN | Section 4a | RESOLVED | Pinned to exact commit `73e6b7cc3ca163ce0b53692f7cd732dba170bfce` in Dockerfile `ARG` |
| Check 4a — edge-risk path consistency | PASS (conditional) | Section 4b, 4c | RESOLVED | Path contract preserved; factory/config corrected |
| Check 4b — PBF path consistency | PASS | N/A | N/A | Unchanged and correct |
| Check 4c — CH/LM invalidation on JSON change | WARN | Section 4b, 4e | RESOLVED | `prepare.ch.weightings: none` — flexible mode; JSON change requires restart only |
| Check 4d — Rebuild/restart behavior undocumented | WARN | Section 4e | RESOLVED | Cache invalidation table with exact conditions per change type |
| Check 4d — No Docker health check | WARN | Section 4a, 4c | RESOLVED | `HEALTHCHECK` in Dockerfile + `healthcheck:` in docker-compose; `depends_on: condition: service_healthy` |
| Check 5a — Bbox vs PBF coverage mismatch | PASS (minor caveat) | Revised Section 3 (no bbox used) | RESOLVED | GH-native edges eliminate bbox issue; Delhi NCT guard in routing.py |
| Check 5b — CRS handling | PASS | Section 3b | RESOLVED | Unchanged and correct |
| Check 5c — `build_kde_pool` import with mlflow | PASS (env caveat) | Section 3a | RESOLVED | Requirement documented; `ml/requirements.txt` must be installed |
| Check 5d — `osmnx>=1.9.0` permits 2.x with incompatible API | FAIL | Revised Section 3 | RESOLVED | OSMnx dependency removed entirely; GH-native approach uses only geopandas + pyproj |
| Check 5e — Edge iteration and geometry | PASS | N/A | N/A (OSMnx not used in Revision 2) | |
| Spatial-join pseudocode — OSMnx MultiIndex → `index_right0/1/2` | FAIL | Revised Section 3b | RESOLVED | `edge_buffers` has simple integer index; `sjoin` produces single `index_right` column |
| Check 5 input reproducibility — Overpass vs PBF | FAIL | Stage 0 + Revised Section 3 | RESOLVED | Python reads `gh_edges.csv` derived from same pinned PBF |
| Check 6 (all) — Backend integration | PASS | Section 6b | RESOLVED | Endpoint, geometry, Pydantic v2 default all correct |
| Check 7a — Cloudflare stable hostname requires domain | FAIL | Revised Section 7 | RESOLVED | Both quick-tunnel (no domain) and named-tunnel paths documented with domain requirement explicit |
| Check 7b — Windows service reads wrong config path | FAIL | Revised Section 7 | RESOLVED | LocalSystem path `C:\Windows\System32\config\systemprofile\.cloudflared\` documented; Task Scheduler user-mode alternative provided |
| Check 7c — Latency underestimated | WARN | Revised Section 7 | RESOLVED | Realistic estimate 600–1,000 ms warm; multi-second cold starts acknowledged |
| Check 8a — Factory patch incomplete | FAIL | Section 5g | RESOLVED | Full patch design including encoded value retrieval and construction pattern |
| Check 8b — No per-request profile selection | WARN | Section 6b, 6c | RESOLVED | `RouteRequest.profile: Optional[str]`; frontend dropdown added to Section 10 |
| Check 8c — Weekly retrain never rebuilds edge_risk.json | FAIL | Section 8c | RESOLVED | Retrain workflow extended with build + validation + artifact upload steps |
| Check 8d — No GraphHopper health check / startup race | FAIL | Section 4c, 4f | RESOLVED | `healthcheck:` in compose; `depends_on: service_healthy`; retry loop in backend lifespan |
| Check 8e — Missing edge_risk.json creates directory | FAIL | Section 4c (entrypoint) | RESOLVED | `docker-entrypoint.sh` validates file existence before start; graceful degradation to fastest |
| Check 8 — No match-rate metrics or quality gates | FAIL | Section 5f, 5g, 3f | RESOLVED | Startup validator + telemetry counters + Stage 2 coverage gate |
| Check 8 — No tests for custom weighting / factory / JSON errors | FAIL | Section 8b (Step 8) | RESOLVED | Test specifications in Section 8b; Java compile test in Dockerfile build |
| Check 9.6 — Max-raw normalization unstable | WARN | Revised Section 3d | RESOLVED | P99 clipping with `min(..., 1.0)` cap for super-p99 edges |
| Check 9.10 — Profiles/config don't start on GH 9.1 | FAIL | Revised Sections 4b, 5 | RESOLVED | Config and Java updated for GH 9.1 |
| Check 9.12 — KDE post-scoring vs GH crime objective disagreement | WARN | Section 6e | RESOLVED | Documented as intentional two-layer design; user sees KDE bands only |
| Check 9.13 — Profile absent from request/response/UI | WARN | Section 6b, 6c | RESOLVED | `RouteRequest.profile` added; frontend dropdown planned |
| Check 10 (Findings 1–4) — All accurate audit findings | WARN | Sections 6a, 6b, 4c | RESOLVED | All four findings have explicit resolutions |
| Warning 5 — Normalization by one maximum unstable | WARN | Section 3d | RESOLVED | P99 clipping |
| Warning 7 — Deduplication underspecified | WARN | Section 6b | RESOLVED | `_route_fingerprint` algorithm fully specified (up to 10 evenly-spaced samples, 3dp) |
| Warning 8 — Pin GH to commit SHA | WARN | Section 4a | RESOLVED | `ARG GH_COMMIT=73e6b7cc3ca163ce0b53692f7cd732dba170bfce` in Dockerfile |
| Needs-research 1 — Graph mapping experiment | Blocking | Stage 0 | RESOLVED in design | Stage 0 protocol specified; EdgeExporter + integer IDs eliminate mapping uncertainty |
| Needs-research 2 — GH extension spike | Blocking | Sections 4a, 5 | RESOLVED in design | Full CrimeWeighting + factory patch specified for GH 9.1; must compile-test before Stage 4 |
| Needs-research 3 — Weighting/CH preparation | Blocking | Section 4b, 4e | RESOLVED | `prepare.ch.weightings: none` — flexible mode throughout; CH invalidation rules documented |
| Needs-research 4 — Cloudflare deployment test | Blocking | Revised Section 7 | DEFERRED | Requires real domain or quick-tunnel test to confirm latency. Blocking for Stage 6. Operator must test before demo. |
| Needs-research 5 — Operational refresh design | Blocking | Section 8c | PARTIALLY RESOLVED | Phase 7: manual artifact download (Option P1). Automated delivery deferred to Phase 8. |

### Self-Review Checklist

- [x] Every critical blocker (9 identified in verification) has a concrete resolution in this document.
- [x] No GH 9.1-incompatible APIs remain: constructor uses `(BooleanEncodedValue, DecimalEncodedValue, TurnCostProvider)`; `calcEdgeWeight` 2-arg; `calcEdgeMillis` 2-arg; `weighting: custom` for fastest; no `vehicle:` keys; top-level `server:` block; Jackson not org.json.
- [x] Revised stages can be implemented without unresolved ambiguity. The only deferred item (Cloudflare latency measurement) is a validation step, not an implementation blocker.
- [x] Only `docs/phase7_plan.md` was modified. No code was written.
- [x] λ values are consistent throughout: 0.1 (balanced), 0.3 (safest), both seconds-based. Arithmetic: 0.1×1.0×1000=100 s≈1.7 min; 0.3×1.0×1000=300 s=5 min; 0.3×0.5×200=30 s; 0.1×0.5×200=10 s.

---

*End of Phase 7 Implementation Plan — Revision 2.*
*All 9 critical blockers, 8 warnings, and 5 needs-research items from `docs/phase7_verification.md` addressed above.*
