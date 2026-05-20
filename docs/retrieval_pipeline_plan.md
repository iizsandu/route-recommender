# Retrieval Pipeline — Implementation Plan
## Project 2: Crime-Aware Route Recommender (Delhi)

---

## Context

This plan covers the embedding, indexing, and retrieval pipeline for
Project 2. The data source is the cleaned output of Project 1 —
structured crime records in local MongoDB (`crime2` database).

A validation experiment on 300 records confirmed the architecture is
sound. This plan implements it at full scale (~12K records) with two
fixes applied from the experiment findings.

---

## What We Are Building

Two retrieval features:

**Feature A — Evidence Retrieval**
Given a route (sequence of lat/lng points), sample points along the
route at regular intervals and retrieve historically reported incidents
near each point. Used by the route risk scorer to explain *why* a
route has a given risk score.

**Feature D — Semantic Search**
Given a free-text query, return the top K most relevant past incidents
across all of Delhi. Displayed as a search result list (incident cards
or map pins). No LLM generation — retrieval only.

Neither feature predicts future crime. All outputs must be framed as
*"historically reported incidents"* in the UI and API responses.

---

## Stack (Locked — Do Not Change)

| Component | Choice |
|---|---|
| Summarization | `sshleifer/distilbart-cnn-6-6` |
| Embedding model | `BAAI/bge-small-en-v1.5` (384 dims) |
| Sparse vectors | `rank_bm25` (BM25, not TF-IDF) |
| Vector DB | Qdrant, self-hosted in Docker |
| Fusion | Reciprocal Rank Fusion (RRF, k=60) |
| API | FastAPI |
| Data source | Local MongoDB `crime2.extracted` + `crime2.articles2` |

---

## Repository Layout

```
project2/
├── ml/
│   └── data/
│       ├── category_mapping.py   # already exists — do not modify
│       ├── ingest.py             # already exists — do not modify
│       └── validate.py           # already exists — do not modify
├── retrieval/
│   ├── __init__.py
│   ├── summarise.py              # distilbart batch summarization
│   ├── embed.py                  # bge-small embedding
│   ├── bm25_index.py             # BM25 sparse vector computation
│   ├── qdrant_store.py           # collection creation + upsert
│   ├── search.py                 # hybrid query + RRF fusion
│   └── pipeline.py               # orchestrates full ingest → index run
├── api/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app
│   ├── routers/
│   │   ├── search.py             # Feature D endpoint
│   │   └── route.py              # Feature A endpoint
│   └── schemas.py                # Pydantic request/response models
├── scripts/
│   └── build_index.py            # one-time script: MongoDB → Qdrant
├── requirements.txt
├── docker-compose.yml            # Qdrant service
└── CLAUDE.md                     # this file's runtime companion
```

---

## Data Flow

```
MongoDB crime2.extracted
        │
        ▼
[1] Pull records (is_crime=True, has valid Delhi coords)
        │
        ▼
[2] Join with crime2.articles2 on url → get article text
        │
        ▼
[3] summarise.py — distilbart → distilbart_summary (one-time batch)
        │
        ▼
[4] embed.py — bge-small → dense vector (384 dims, L2-normalised)
        │
        ▼
[5] bm25_index.py — rank_bm25 → sparse vector (indices + values)
        │
        ▼
[6] qdrant_store.py — upsert point with payload + both vectors
        │
        ▼
   Qdrant collection: delhi_crimes
        │
   ┌────┴────┐
   ▼         ▼
Feature A  Feature D
(geo+text) (text only)
```

---

## Module Specifications

### `retrieval/summarise.py`

**Purpose:** Batch-summarize article texts using distilbart. CPU or GPU.

**Key decisions:**
- Input: list of raw article text strings
- Output: list of summary strings (same order, same length)
- Max input tokens: 1024 (truncate, do not crash)
- Max summary length: 120 tokens, min 30
- Batch size: 16 (GPU) or 4 (CPU) — auto-detect device
- Empty or very short texts (<50 chars): return the text as-is,
  do not pass to model
- Use `BartTokenizer` + `BartForConditionalGeneration` directly,
  NOT `pipeline("summarization")` — the pipeline API is broken on
  newer transformers versions

**Interface:**
```python
def summarize_batch(texts: list[str], batch_size: int = 16) -> list[str]:
    ...
```

---

### `retrieval/embed.py`

**Purpose:** Encode text strings to L2-normalised dense vectors using
bge-small.

**Key decisions:**
- Model: `BAAI/bge-small-en-v1.5`
- Always normalise embeddings (`normalize_embeddings=True`)
- Batch size: 64
- Returns: numpy array of shape (N, 384)

**Interface:**
```python
def encode(texts: list[str]) -> np.ndarray:
    ...
```

---

### `retrieval/bm25_index.py`

**Purpose:** Compute BM25 sparse vectors for the corpus and for
individual queries at search time.

**Key decisions:**
- Use `rank_bm25.BM25Okapi` — do NOT use TF-IDF as a substitute
- Tokenisation: lowercase, split on whitespace and punctuation
- At index time: fit BM25 on the full corpus of summaries, then
  compute per-document sparse vectors (top-N non-zero terms only,
  N=200 to keep vectors manageable)
- At query time: score the query against the fitted BM25 vocabulary
- The fitted BM25 object must be serialised to disk (pickle) so it
  can be reloaded at API serving time without re-fitting
- Sparse vector format: `SparseVector(indices=[...], values=[...])`
  where indices are vocabulary term indices and values are BM25 scores

**Interface:**
```python
def fit(corpus: list[str]) -> BM25Okapi:
    ...

def corpus_sparse_vector(bm25: BM25Okapi, doc_tokens: list[str],
                          top_n: int = 200) -> SparseVector:
    ...

def query_sparse_vector(bm25: BM25Okapi,
                         query: str) -> SparseVector:
    ...

def save(bm25: BM25Okapi, path: Path) -> None:
    ...

def load(path: Path) -> BM25Okapi:
    ...
```

---

### `retrieval/qdrant_store.py`

**Purpose:** Create and manage the Qdrant collection. Upsert points.

**Key decisions:**
- Collection name: `delhi_crimes`
- Dense vector name: `"dense"`, size 384, cosine distance
- Sparse vector name: `"sparse"`, on_disk=False
- Payload fields per point (all required):
  - `crime_macro` (str)
  - `crime_type` (str)
  - `lat` (float or null)
  - `lng` (float or null)
  - `crime_date` (str ISO format or null)
  - `article_date` (str ISO format or null)
  - `url` (str)
  - `summary` (str)
  - `location_exact` (str or null)
  - `severity_score` (int or null)
- Use `client.create_collection` with existence check — do not use
  deprecated `recreate_collection`
- Upsert in batches of 100 to avoid memory pressure
- Point ID: use positional integer index (0-based)

**Interface:**
```python
def get_client(host: str = "localhost", port: int = 6333) -> QdrantClient:
    ...

def create_collection(client: QdrantClient,
                       collection_name: str = "delhi_crimes") -> None:
    ...

def upsert_batch(client: QdrantClient, points: list[PointStruct],
                  collection_name: str = "delhi_crimes") -> None:
    ...
```

---

### `retrieval/search.py`

**Purpose:** Hybrid search with RRF fusion. Core retrieval logic for
both features.

**Key decisions:**

Geo filter (Feature A only):
```python
import math
lat_delta = radius_km / 111.0
lng_delta = radius_km / (111.0 * math.cos(math.radians(query_lat)))
```
This corrects for longitude compression at Delhi's latitude (~28°N).
Do NOT use a fixed 111 divisor for both lat and lng — that was a
validated bug from the experiment.

RRF fusion:
- k = 60 (standard constant)
- Score = sum of 1/(rank + 1 + k) across dense and sparse result lists
- Fetch `top_k * 2` from each list before fusing, return top_k after

Response: return structured dicts, not raw Qdrant objects. Each result
must include `rrf_score`, all payload fields, and whether it was
matched by dense, sparse, or both (for debugging).

**Interface:**
```python
def hybrid_search(
    client: QdrantClient,
    embed_model: SentenceTransformer,
    bm25: BM25Okapi,
    query_text: str,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 2.0,
    top_k: int = 10,
    collection_name: str = "delhi_crimes",
) -> list[dict]:
    ...
```

---

### `retrieval/pipeline.py`

**Purpose:** Orchestrates the full MongoDB → Qdrant index build.
Runs once to build the index; re-run to rebuild from scratch.

**Steps in order:**
1. Connect to MongoDB, pull all `is_crime=True` records from
   `extracted` with valid Delhi coordinates
   (lat: 28.0–29.5, lng: 76.5–78.0)
2. Join with `articles2` on `url` to get article text
3. Log how many records have no matching article text (expected ~0)
4. Summarize all article texts with distilbart (batch, with progress bar)
5. Embed all summaries with bge-small
6. Fit BM25 on all summaries, compute per-document sparse vectors
7. Save BM25 model to `retrieval/bm25_model.pkl`
8. Create Qdrant collection (skip if already exists and
   `--rebuild` flag not passed)
9. Upsert all points in batches of 100
10. Log final point count and confirm against input record count

**CLI flags:**
- `--rebuild`: drop and recreate the collection before upserting
- `--dry-run`: run steps 1–6 only, skip Qdrant upsert
- `--limit N`: only process first N records (for testing)

---

### `scripts/build_index.py`

Thin CLI wrapper around `retrieval/pipeline.py`. Entry point for
running the index build:

```bash
python scripts/build_index.py --rebuild
python scripts/build_index.py --limit 500 --dry-run
```

---

### `api/schemas.py`

```python
class SearchRequest(BaseModel):
    query: str                    # required
    top_k: int = 10               # default 10, max 50

class RoutePoint(BaseModel):
    lat: float
    lng: float

class RouteRequest(BaseModel):
    waypoints: list[RoutePoint]   # ordered route points
    sample_interval_m: float = 500.0  # sample every N metres
    radius_km: float = 2.0
    top_k_per_point: int = 5
    crime_macro_filter: str | None = None  # optional category filter

class IncidentResult(BaseModel):
    rrf_score: float
    crime_macro: str
    crime_type: str
    lat: float | None
    lng: float | None
    crime_date: str | None
    summary: str
    url: str
    location_exact: str | None
    severity_score: int | None

class SearchResponse(BaseModel):
    query: str
    results: list[IncidentResult]
    total_returned: int
    framing_note: str = (
        "Results represent historically reported incidents from news "
        "sources. This is not a prediction of future crime."
    )

class RouteResponse(BaseModel):
    sampled_points: int
    incidents_by_point: list[dict]   # list of {lat, lng, incidents}
    framing_note: str = (
        "Risk indicators are based on historically reported incidents "
        "near each route point. Not a prediction of future crime."
    )
```

---

### `api/routers/search.py` — Feature D endpoint

```
POST /api/v1/search
Body: SearchRequest
Response: SearchResponse
```

Loads bge-small and BM25 model at startup (FastAPI lifespan). Does
not reload on every request.

---

### `api/routers/route.py` — Feature A endpoint

```
POST /api/v1/route/incidents
Body: RouteRequest
Response: RouteResponse
```

Samples points along the route at `sample_interval_m` metre intervals
(linear interpolation between waypoints). Runs `hybrid_search` at
each sampled point with the geo filter active. Deduplicates incidents
that appear at multiple sample points.

---

### `docker-compose.yml`

```yaml
services:
  qdrant:
    image: qdrant/qdrant:v1.13.4
    ports:
      - "6333:6333"
    volumes:
      - ./qdrant_storage:/qdrant/storage
```

Use `v1.13.4` explicitly — earlier versions (≤1.9.2) do not support
the `query_points` API used by current qdrant-client.

---

## Known Limitations to Carry Forward

1. **Underreporting bias** — sexual violence incidents are severely
   underrepresented in news-sourced data. The pipeline must not be
   used to claim an area is "safe" for women based on low incident
   counts alone.

2. **Dense embedding quality** — bge-small embeddings show weak
   crime-type discrimination on this corpus (all Delhi crime news
   shares boilerplate language). BM25 carries more retrieval weight
   for keyword queries. This is expected and accepted.

3. **Month is not a valid feature** — do not add `crime_date.month`
   as a filter or ranking signal anywhere. Apparent seasonality in
   the data reflects scraping cadence, not real crime patterns.

4. **Non-Delhi coordinates** — some records have coordinates outside
   Delhi-NCR bounds (28.0–29.5 lat, 76.5–78.0 lng). These are
   excluded at index build time in `pipeline.py` step 1. Do not
   include them.

---

## What Not to Build

- No LLM generation on top of retrieval results (no RAG)
- No crime prediction or forecasting in this pipeline
- No `weapon_used` as a search or filter field — it is a data
  leakage risk
- No `month` or `season` filters

---

## Environment

- Local machine, Windows
- Qdrant in Docker (see docker-compose.yml)
- Python 3.12
- MongoDB local at `mongodb://localhost:27017/`, database `crime2`
- Near-zero cloud budget — all inference runs locally
