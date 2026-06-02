# Personalised Incident Retrieval — How It Works

## Overview

When a user searches for a route, the app surfaces nearby historical crime incidents along that route. The **general incidents** feature does this with a fixed safety-oriented query — every user gets the same list of nearby crimes regardless of their context.

**Personalised incidents** goes further. The user answers three questions about their travel situation, and the system returns incidents that are semantically relevant to *their specific context* — not just geographically nearby. A lone woman walking to an isolated road at night will see different incidents than a group arriving at a busy market by cab.

### General vs. Personalised at a glance

| | General Incidents | Personalised Incidents |
|---|---|---|
| Query text | Fixed: `"crime robbery assault kidnapping safety woman female"` | User's situation sentence |
| Ranking signal | Geo proximity (primary) + fixed keyword match | Semantic similarity to situation + keyword match + geo |
| Result focus | All female-safety crimes near route | Crimes semantically matching described context |
| Deduplication | Per route | Across all waypoints, sorted by relevance score |
| Map rendering | Solid coloured dot (18×18, category colour) | Pulsing purple dot (40×40, SMIL animation) |
| Reset | Switch route | "Reset to general results" button |

---

## Tech Stack

| Component | Technology | Version | Purpose |
|---|---|---|---|
| Dense embedding model | BAAI/bge-small-en-v1.5 | — | Encode text → 384-dim float vector |
| Embedding library | sentence-transformers | 2.7.0 | Load and run bge-small on CPU |
| Sparse retrieval | BM25Okapi (rank-bm25) | 0.2.2 | Keyword frequency scoring |
| Vector database | Qdrant Cloud (free tier) | qdrant-client 1.9.1 | Store + search dense + sparse vectors |
| Fusion algorithm | Reciprocal Rank Fusion (RRF) | — | Merge dense + sparse result lists |
| Article summarisation | distilbart-cnn-6-6 | transformers 4.40.0 | Index-build only — compress raw news articles |
| Backend framework | FastAPI | 0.110.0 | Serve `POST /routes/incidents/personalised` |
| Frontend | React 18 + MapLibre GL JS | — | Questionnaire UI + pulsing dot markers |

---

## High-Level Workflow

```
┌─────────────────────────────────────────────────────────┐
│  OFFLINE (one-time index build)                         │
│                                                         │
│  MongoDB (6,775 crime records)                          │
│    ↓ article text fetched                               │
│  distilbart-cnn-6-6 → article summary                   │
│    ↓                                                    │
│  Augmented text: crime_type + location + victim +       │
│                  weapon + summary                       │
│    ↓                         ↓                          │
│  bge-small-en-v1.5       BM25Okapi.fit(all texts)       │
│  → 384-dim float vector   → BM25 vocabulary             │
│    ↓                         ↓                          │
│  dense vector per doc     sparse vector per doc         │
│  (top 200 BM25 terms)                                   │
│    └──────────────┬──────────┘                          │
│                   ↓                                     │
│  Qdrant Cloud: upsert 6,775 points                      │
│  bm25_model.pkl saved → baked into Docker image         │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  ONLINE (per user request)                              │
│                                                         │
│  User answers 3-question questionnaire                  │
│    ↓                                                    │
│  Situation sentence constructed                         │
│  "Woman travelling Alone by Walking arriving at         │
│   Isolated or poorly lit road"                          │
│    ↓                                                    │
│  POST /routes/incidents/personalised                    │
│  { situation, waypoints, radius_km=2.0, max_total=8 }   │
│    ↓                                                    │
│  Waypoints sampled every 500 m                          │
│    ↓ for each sampled point:                            │
│  Geo bounding-box filter (2 km radius, lat-corrected)   │
│    ↓                         ↓                          │
│  Dense query:             Sparse query:                 │
│  bge-small encodes        BM25 tokenises situation      │
│  situation text           → sparse vector               │
│  → 384-dim query vector                                 │
│    ↓                         ↓                          │
│  Qdrant cosine search    Qdrant BM25 dot-product search  │
│  (filtered to geo box)   (filtered to geo box)          │
│    ↓                         ↓                          │
│  ranked list A            ranked list B                 │
│    └──────────────┬──────────┘                          │
│                   ↓                                     │
│  RRF fusion: score = 1/(rank+1+60)                      │
│  combined, deduplicated by URL                          │
│  sorted by rrf_score desc, truncate to max_total        │
│    ↓                                                    │
│  Return incident list to frontend                       │
│    ↓                                                    │
│  MapView: pulsing purple dots on map                    │
│  Panel: incident cards with summary + source link       │
└─────────────────────────────────────────────────────────┘
```

---

## Part 1: Offline Index Build

This runs once (or when new crime data is added). It processes all crime records and stores them in Qdrant so they can be searched at serving time.

### Step 1 — Source data

Records are fetched from MongoDB (`crime2` database):
- `extracted` collection: structured crime fields (`crime_type`, `location_exact`, `victim`, `weapon_used`, `coordinates`, `crime_date`, `url`)
- `articles2` collection: raw news article text, joined by URL

6,775 records pass the Delhi-NCR bounds filter (lat 28.0–29.5, lng 76.5–78.0).

### Step 2 — Article summarisation

Raw news articles can be thousands of words. Each article text is compressed using **distilbart-cnn-6-6** (a distilled version of BART fine-tuned on CNN/DailyMail summarisation). This runs on GPU locally — it's the slowest step of the index build (~11 minutes for 6,775 records).

The summary captures the who/what/where of the incident in 2–4 sentences, which is more useful as an embedding input than the full article.

### Step 3 — Augmented text construction

A summary alone loses structured signal. Instead, one combined string is built per record:

```
"{crime_type}. {location_exact}. {victim}. {weapon_used}. {distilbart_summary}"
```

Example:
```
"Robbery. Connaught Place Metro Station. 22-year-old woman. knife.
Two men followed a woman from the metro exit and snatched her bag at
knifepoint before fleeing on a motorcycle."
```

Why augment: if a user's situation mentions "robbery" or "metro", the BM25 keyword match is strengthened. If it mentions "woman alone at night", the dense embedding picks up the semantic similarity to "woman followed" in the summary.

### Step 4 — Dense embedding (bge-small-en-v1.5)

Each augmented text is passed through **BAAI/bge-small-en-v1.5**:

- **Dimensions:** 384 float32 values
- **Normalisation:** L2-normalised — every vector has unit length (magnitude = 1)
- **Distance metric:** Cosine similarity. Because vectors are L2-normalised, cosine similarity equals dot product, which Qdrant can compute very fast with HNSW indexing
- **Why bge-small:** Strong performance on retrieval benchmarks (MTEB), small model (~130 MB), runs on CPU in the backend container without needing GPU

What L2 normalisation means in practice: two vectors with angle 0° between them (identical direction) have cosine similarity = 1.0 (perfect match). Two vectors with angle 90° have similarity = 0.0 (completely different meaning).

**Qdrant collection config:**
```python
VectorParams(size=384, distance=Distance.COSINE)
```

### Step 5 — BM25 sparse vectors

**BM25 (Best Matching 25)** is a classical keyword-matching algorithm. It scores documents based on term frequency (how often a query word appears in a document) adjusted for document length.

#### How BM25 works (simplified)

For a query term `t` and document `d`:

```
BM25(t, d) ∝  tf(t,d) × (k1+1)   ×  log( (N - df(t) + 0.5) / (df(t) + 0.5) )
              ──────────────────
              tf(t,d) + k1×(1-b+b×|d|/avgdl)
```

Where:
- `tf(t, d)` = how many times term `t` appears in document `d`
- `df(t)` = how many documents contain term `t` (rarer terms score higher)
- `N` = total number of documents
- `|d|` = document length, `avgdl` = average document length
- `k1`, `b` = tuning parameters (BM25Okapi defaults: k1=1.5, b=0.75)

The result: common words like "the" score near zero; rare, specific words like "knifepoint" or "Connaught" score high.

#### How sparse vectors are built

```python
# Tokenizer: lowercase, alphanumeric runs only
def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())

# Per document: compute BM25 score for each vocabulary term
scores = bm25.get_scores(doc_tokens)

# Keep top 200 highest-scoring terms (most articles use < 200 unique terms)
top_indices = scores.argsort()[::-1][:200]
top_indices = top_indices[scores[top_indices] > 0]  # drop zero-score terms

# Result: a sparse vector
SparseVector(
    indices=[42, 1847, 203, ...],   # vocabulary term indices
    values=[2.41, 1.87, 1.23, ...], # BM25 scores
)
```

Why sparse: most of the ~50,000 vocabulary positions are zero for any given document. Storing only the non-zero positions saves memory and makes search fast.

The fitted BM25 model (`bm25_model.pkl`) encodes the vocabulary and document statistics. It is saved to `retrieval/bm25_model.pkl` and **baked into the Docker image** at build time so the backend can compute query sparse vectors at serving time without re-fitting.

### Step 6 — Qdrant upsert

Each of the 6,775 records is stored as a Qdrant point:

```python
PointStruct(
    id=idx,
    vector={
        "dense": [0.021, -0.043, ...],    # 384 floats, L2-normalised
        "sparse": SparseVector(...)        # top-200 BM25 terms
    },
    payload={
        "crime_macro": "Robbery",
        "crime_type": "Snatching",
        "lat": 28.6315,
        "lng": 77.2167,
        "crime_date": "2025-03-12 00:00:00",
        "url": "https://...",
        "summary": "Two men followed a woman...",
        "location_exact": "Connaught Place Metro Station",
        "victim": "22-year-old woman",
        "weapon_used": "knife",
    }
)
```

Records are upserted in batches of 100. The collection in Qdrant Cloud (AWS sa-east-1 free tier) stores both the vector index (HNSW for dense, inverted index for sparse) and the payload fields.

---

## Part 2: Online Serving Path

This runs on every "Personalise these results" submission.

### Step 1 — Frontend questionnaire

The user answers 3 questions in the side panel:

| Question | Options |
|---|---|
| Are you travelling alone? | Alone / With one other person / In a group |
| How are you travelling? | Walking / Auto-rickshaw / Cab or taxi / Own vehicle |
| What describes your destination? | Busy market or commercial area / Residential neighbourhood / Office or institution / Isolated or poorly lit road |

These three answers are combined into one situation sentence:

```js
const situation =
  `Woman travelling ${answers.travelling_with} by ${answers.transport_mode} arriving at ${answers.destination_type}`
```

Example: `"Woman travelling Alone by Walking arriving at Isolated or poorly lit road"`

This is the **query text** that gets embedded and searched against the index.

### Step 2 — API call

```
POST /routes/incidents/personalised
{
  "situation": "Woman travelling Alone by Walking arriving at Isolated or poorly lit road",
  "waypoints": [[28.655, 77.232], [28.651, 77.229], ...],
  "radius_km": 2.0,
  "max_total": 8
}
```

Note: GeoJSON route coordinates come as `[lng, lat]`. The frontend reverses them to `[lat, lng]` before sending.

### Step 3 — Waypoint sampling

The ORS route geometry has a waypoint every ~20 metres — a 15 km route has ~750 waypoints. Querying Qdrant for every waypoint with a 2 km radius would produce massive overlap (750 queries × 2 km coverage each).

Instead, waypoints are **sampled every 500 metres** using Haversine distance:

```
750 waypoints every 20 m  →  ~30 sampled waypoints every 500 m
```

This gives 5–8 Qdrant queries per route instead of 100+, with the same geographic coverage (2 km radius still covers the gaps). The first and last waypoints are always included.

### Step 4 — Geo bounding-box pre-filter

Before any vector search, Qdrant filters to only the records within a 2 km bounding box around the sampled waypoint:

```python
lat_delta = radius_km / 111.0
lng_delta = radius_km / (111.0 * cos(radians(lat)))
```

**Why latitude-corrected longitude:** at Delhi's latitude (~28.6°N), 1 degree of longitude spans approximately 97.4 km, not the 111 km it spans at the equator. Using 111 for both axes would make the east-west search radius ~12% too wide, pulling in incidents from further away than intended. The `cos(lat)` correction fixes this.

The filter is applied as a server-side `FieldCondition` range on the `lat` and `lng` payload fields — Qdrant executes it before the vector search, so only records within the bounding box are candidates.

### Step 5 — Dense search

The situation text is passed through the same **bge-small-en-v1.5** model that encoded the index:

```
"Woman travelling Alone by Walking arriving at Isolated or poorly lit road"
→ [0.031, -0.012, 0.087, ...]   # 384-dim query vector, L2-normalised
```

Qdrant searches the HNSW index for the top-k records with the highest cosine similarity to this query vector, within the geo-filtered candidates.

**What cosine similarity measures here:** how closely the *meaning* of the situation sentence matches the *meaning* of each crime record's augmented text. A query about "walking alone at night" will score high against summaries that mention women walking, being followed on foot, street-level incidents — even if the exact words differ.

### Step 6 — Sparse (BM25) search

The same situation text is tokenised and scored against the fitted BM25 vocabulary:

```
"Woman travelling Alone by Walking arriving at Isolated or poorly lit road"
→ tokens: ["woman", "travelling", "alone", "by", "walking", "arriving", "at", "isolated", "or", "poorly", "lit", "road"]
→ SparseVector(indices=[2847, 1203, ...], values=[1.42, 0.98, ...])
```

Qdrant searches the sparse inverted index for records with the highest BM25 dot product against this query sparse vector, within the same geo-filtered candidates.

**What BM25 catches here:** exact keyword matches. If the user selects "Walking" and a crime record contains the word "walking" prominently, BM25 scores it highly — even if the embedding similarity is only moderate.

### Step 7 — RRF fusion

Dense search and sparse search each return an independent ranked list. **Reciprocal Rank Fusion (RRF)** merges them into one ranking:

```python
_RRF_K = 60

def _rrf_score(rank: int) -> float:
    return 1.0 / (rank + 1 + _RRF_K)
```

Each document gets a score contribution from its rank in each list:

| Rank in dense list | RRF score | Rank in sparse list | RRF score | Combined |
|---|---|---|---|---|
| 0 (best) | 1/(1+60) = 0.0161 | 2 | 1/(3+60) = 0.0159 | 0.0320 |
| 1 | 1/(2+60) = 0.0161 | — (not found) | 0 | 0.0161 |
| — (not found) | 0 | 0 (best) | 0.0161 | 0.0161 |

A document appearing in the top 5 of **both** lists scores higher than one ranked #1 in only one list. This is RRF's key property: it rewards consistent relevance across multiple signals.

**Why k=60:** the constant from the original RRF paper (Cormack et al., 2009). It prevents the rank-1 document from dominating when list lengths differ, and makes the score differences between top ranks smoother.

The final combined scores are normalised and stored as `rrf_score` on each result dict.

### Step 8 — Deduplication, sort, truncate

Results from all sampled waypoints are merged:

1. **Deduplicate by URL** — the same news article might appear near multiple waypoints along the route. Only the first occurrence (highest rrf_score) is kept.
2. **Sort by `rrf_score` descending** — most relevant incidents first.
3. **Truncate to `max_total`** (default 8) — prevents the panel from being overwhelmed.

---

## What the User Sees

### Panel (RouteResults)

Up to 8 incident cards, each showing:
- Crime macro category with a colour dot (e.g. 🔴 Sexual Violence, 🟠 Robbery)
- Summary text (truncated to 180 characters)
- Victim (if available)
- Weapon used (if available)
- Location and/or date
- "Source ↗" link to the original news article

A "Reset to general results" button reverts to the standard nearby incidents list.

### Map (MapView)

Personalised incident locations are rendered as **pulsing purple dots** — distinct from the solid category-coloured dots used for general incidents.

The pulse is implemented as SVG SMIL animation (not CSS `transform: scale`):

```svg
<svg width="40" height="40" viewBox="0 0 40 40">
  <!-- outer pulse ring -->
  <circle cx="20" cy="20" fill="#7f77dd" fillOpacity="0.15">
    <animate attributeName="r" values="9;28;28" dur="2s" repeatCount="indefinite" begin="0.3s" />
    <animate attributeName="fill-opacity" values="0.15;0;0" dur="2s" repeatCount="indefinite" begin="0.3s" />
  </circle>
  <!-- inner pulse ring -->
  <circle cx="20" cy="20" fill="#7f77dd" fillOpacity="0.3">
    <animate attributeName="r" values="9;20;20" dur="2s" repeatCount="indefinite" />
    <animate attributeName="fill-opacity" values="0.3;0;0" dur="2s" repeatCount="indefinite" />
  </circle>
  <!-- solid core -->
  <circle cx="20" cy="20" r="9" fill="#7f77dd" />
  <circle cx="20" cy="20" r="9" fill="none" stroke="white" strokeWidth="2" />
</svg>
```

CSS `transform: scale` was tried first but clips inside MapLibre's marker container div. SMIL animates the SVG `r` attribute directly within the SVG coordinate space, so it expands outward without clipping.

Clicking a dot opens a popup with the incident summary and source link.

---

## Key Numbers

| Parameter | Value | Source |
|---|---|---|
| Records in Qdrant | 6,775 | `scripts/build_index.py` output |
| Vector dimensions | 384 | `embed.py` — bge-small-en-v1.5 |
| Sparse vector max terms | 200 | `bm25_index.py` — `corpus_sparse_vector()` |
| RRF k constant | 60 | `search.py` — `_RRF_K` |
| Waypoint sample interval | 500 m | `retrieval_service.py` — `_SAMPLE_INTERVAL_M` |
| Search radius | 2.0 km (default) | `retrieval_service.py` — `get_personalised_incidents()` |
| Max results returned | 8 (default) | `retrieval_service.py` — `max_total` |
| Qdrant Cloud tier | Free (1 GB, AWS sa-east-1) | Qdrant Cloud dashboard |

---

## Why Hybrid Search (Dense + Sparse) Over Either Alone

**Dense-only problems:**
- May miss exact keyword matches (e.g. a specific locality name that the embedding model doesn't associate strongly with the query)
- Computationally heavier per query

**BM25-only problems:**
- Misses synonyms and paraphrasing ("walking" vs "pedestrian", "attacked" vs "assaulted")
- Fails completely when the query uses words not in the corpus vocabulary

**Hybrid (dense + BM25 + RRF):**
- Dense handles semantic gap (different words, same meaning)
- BM25 handles exact matches (same words, high precision)
- RRF rewards documents that score well on both — the most reliable signal of relevance

This is the standard approach used in production retrieval systems (Elasticsearch, Qdrant, Pinecone all support hybrid search for this reason).
