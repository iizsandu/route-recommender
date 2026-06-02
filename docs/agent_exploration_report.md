# Personalised Recommendation AI Agent — Exploration Report

> **Purpose:** Pre-decision feasibility study. No code has been written. Every finding
> is derived from reading the actual source files. Recommendations are flagged but all
> decisions remain with you.

---

## 1. Codebase Summary

### Architecture

The system is a stateless, anonymous web app. There is no user identity at any layer.

```
Frontend (Vercel)          Backend (Azure Container Apps)        External Services
─────────────────          ──────────────────────────────        ─────────────────
React 18 + MapLibre   ───► FastAPI (single service)        ───► ORS (routing)
                            ├── KDE risk scoring                 Mappls / Nominatim
                            ├── Qdrant hybrid search        ───► Qdrant Cloud (vectors)
                            ├── LightGBM ensemble (flag)         Azure Cosmos DB (crimes)
                            └── structlog + Prometheus       ───► MLflow (model registry)
```

### What Data Exists

| Data Source | Records | Fields Available for an Agent |
|---|---|---|
| Cosmos DB (structured_crimes) | 8,797 | crime_type, crime_macro, lat, lng, crime_date, location_exact, victim, weapon_used, severity_score, url |
| Qdrant Cloud (delhi_crimes) | 6,775 | All Cosmos fields + distilbart summary + dense vector + BM25 sparse vector |
| KDE artifacts (ml/artifacts/) | 8 categories | Per-location density scores for Sexual Violence, Kidnapping, Robbery, Murder, Assault, Terrorism, Theft, Drug |
| LightGBM artifacts | 4 models | H3 cell risk probabilities for Sexual Violence, Robbery, Assault, global |
| Heatmap PNGs | 7 images | Pre-rendered per-category risk rasters |
| Per-request (ephemeral) | — | origin, destination, depart_time, situation text (3 answers), selected route geometry |

### What Data Does NOT Exist

- **No user identity** — anonymous, no auth, no cookies
- **No user history** — no record of past searches, past routes, past situation answers
- **No feedback signals** — no "did you feel safe?", no route completion, no incident reports
- **No time-series crime data** — all records are historical scrapes, no real-time feed
- **No user preferences** — nothing persisted beyond the disclaimer-acknowledged flag in localStorage

### Current Personalisation Capability

The existing "Personalise these results" feature constructs a situation sentence from 3 dropdown answers and uses it as an embedding query against the Qdrant index. Results are semantically ranked against that sentence. This is the only personalisation in the system and it is entirely stateless — nothing is stored between requests.

---

## 2. Feasibility Analysis

### Where an Agent Could Plug In

There are five natural integration points, each with different data access and value delivered:

```
[A] Pre-route query enhancement
    User situation → LLM expands query → better embedding → better incident retrieval

[B] Post-route safety advisory (NEW ENDPOINT)
    Route scored + incidents retrieved → Agent synthesises → plain-language safety brief

[C] Conversational journey planner (REPLACE FORM)
    User types free-form intent → Agent with tools → structured route + advice

[D] Background risk monitor (ASYNC WORKER)
    New crime records ingested → Agent scores affected routes → alerts

[E] Preference learning loop (FUTURE STATE)
    User chooses routes, gives feedback → Agent updates user profile → personalised reranking
```

### Feasibility by Integration Point

| Point | Feasibility Now | Blocker | Value Delivered |
|---|---|---|---|
| A — Query expansion | **High** | None | Marginal (existing retrieval already works) |
| B — Safety advisory | **High** | Anthropic API key + cost | High — directly useful to target user |
| C — Conversational planner | **Medium** | Latency (3-10s), streaming UI needed | Very high UX improvement |
| D — Risk monitor | **Low** | No user identity to notify, no persistent storage | None without auth |
| E — Preference learning | **None** | No user identity, no history, no feedback | Blocked entirely |

### Data Gaps and What They Block

| Gap | Blocks |
|---|---|
| No user identity | Options D, E entirely; C partially (can't save preferences) |
| No user history | Preference learning, "users like you" recommendations |
| No real-time crime feed | Proactive alerting, live risk updates |
| No user feedback | Reinforcement learning, rating-based reranking |
| No time-series crime data | Hour-of-day risk models, day-of-week patterns |

### Latency Budget

Current route response: ~3–5 s (geocoding + ORS + KDE scoring + Qdrant retrieval)
Azure Container Apps cold start: ~8–12 s (first request after scale-to-zero)

LLM addition scenarios:
- **Inline (sync):** adds 2–10 s — total response 10–15 s. Risky for mobile UX.
- **Streaming (SSE):** first token in ~0.5 s, streams the rest. Feels fast.
- **Background task:** route returned immediately, advisory arrives via polling or SSE ~3 s later.

### Cost Budget

Current infra: ₹0–200/month (all free tiers). Budget headroom: ~₹300–500/month (~$3.50–6.00).

| Model | Input | Output | Cost at 100 calls/day (1k tokens in, 500 out) |
|---|---|---|---|
| Claude Sonnet 4.6 | $3/1M | $15/1M | ~$0.75/day → **$22/month** — over budget |
| Claude Haiku 4.5 | $0.25/1M | $1.25/1M | ~$0.06/day → **$1.80/month** — within budget |
| GPT-4o-mini | $0.15/1M | $0.60/1M | ~$0.045/day → **$1.35/month** — within budget |
| Local (Ollama) | $0 | $0 | $0 — but needs a VM, adds ops complexity |

**Recommendation flag:** Claude Haiku 4.5 is the right model for a production feature within this budget. Sonnet is appropriate only for demos or infrequent use.

---

## 3. RabbitMQ Assessment

### Is RabbitMQ Currently Used?

**No.** There is no message queue, task queue, or event bus anywhere in the stack. The backend is purely synchronous HTTP, with one exception: a FastAPI BackgroundTask is used for the MLflow model hot-reload loop (Python `asyncio` — not a queue).

### When RabbitMQ Would Make Sense

RabbitMQ is a durable message broker designed for:
- **Decoupling producers and consumers** — backend queues a task, a worker processes it later
- **Load levelling** — absorb request spikes; workers drain the queue at their own pace
- **Fan-out** — one event triggers multiple consumers (e.g. new crime record → re-score routes AND send notifications)
- **Retry / dead-letter queues** — failed LLM calls can be retried without the user waiting

It makes strong sense when:
1. You have long-running tasks (>5 s) that users shouldn't wait for
2. You have multiple services that need to react to the same event
3. You need guaranteed delivery (tasks must not be lost on container restart)

### Does It Make Sense Here?

**For the use cases available right now: partially.**

| Use Case | RabbitMQ needed? | Alternative |
|---|---|---|
| Safety advisory (inline) | No | Direct async FastAPI endpoint |
| Safety advisory (background) | Maybe | FastAPI `BackgroundTasks` (already in stack) |
| Streaming advisory | No | Server-Sent Events (SSE) |
| Weekly retrain triggers | No | GitHub Actions cron already handles this |
| New crime → re-score | Yes (if real-time) | But there's no real-time crime feed yet |
| Notify users about risky routes | Yes | But requires user accounts first |

**Assessment:** Adding RabbitMQ now would be premature. There is no multi-consumer fan-out pattern, no user to notify, and no real-time event stream to react to. The FastAPI `BackgroundTasks` mechanism already in the codebase handles the one async use case (model hot-reload). RabbitMQ becomes justified when: (a) user accounts are added, enabling notification delivery, or (b) a real-time crime feed is introduced requiring multiple consumers.

### Viable Alternatives (Already in Stack or Simpler to Add)

| Alternative | Complexity | What it solves |
|---|---|---|
| FastAPI `BackgroundTasks` | Zero (already used) | Fire-and-forget tasks within a request lifecycle |
| FastAPI `asyncio` background task | Zero (already used) | Recurring background loops (hot-reload pattern) |
| Redis + `celery` | Low | Persistent task queue, retry, scheduling |
| Azure Service Bus | Low (Azure ecosystem) | Managed queue, fits existing Azure infra |
| Server-Sent Events (SSE) | Low (FastAPI built-in) | Stream LLM tokens to frontend without polling |
| RabbitMQ | High | Durable multi-consumer event bus |

---

## 4. Decision Tree of Options

### Decision 1: What type of "agent" to build?

```
Option 1A — Safety Advisory Agent
  Input:  scored route + nearby incidents + user situation (if provided)
  Output: 3-5 sentence plain-language safety brief
  Model:  Claude Haiku 4.5 or GPT-4o-mini
  Cost:   ~₹150/month at 100 calls/day
  Effort: Low — 1 new endpoint, 1 system prompt, minimal frontend change
  Value:  High — directly actionable for the target user
  ⭐ Recommended for quick win

Option 1B — Query Expansion Agent
  Input:  user situation sentence
  Output: expanded query (synonyms, related crime terms) for better embedding
  Model:  Claude Haiku or small local model
  Cost:   Very low (tiny calls)
  Effort: Very low — modify get_personalised_incidents() to call LLM before embed
  Value:  Marginal — existing bge-small already handles semantic gap reasonably

Option 1C — Conversational Journey Planner
  Input:  Free-text user message ("I need to go from CP to Lajpat Nagar at 10pm")
  Output: Clarifying questions → structured route request → safety advisory
  Model:  Claude Sonnet 4.6 with tool use (call /routes/recommend, /geocode)
  Cost:   ~₹1,800/month at 100 calls/day — OVER BUDGET for Sonnet
           ~₹150/month with Haiku — within budget but less capable at tool use
  Effort: Medium — new chat UI component, SSE streaming, tool definitions
  Value:  Very high UX — removes the rigid form entirely
  Risk:   Latency (multi-turn tool calls), UI complexity

Option 1D — Autonomous Route Evaluator Agent
  Input:  Multiple routes from ORS
  Output: Agent evaluates each route using tools (score_route, search_incidents)
          and returns structured verdict with reasoning
  Model:  Claude Sonnet 4.6
  Cost:   High — multiple tool call rounds per request
  Effort: Medium
  Value:  High explainability ("Route 1 is safest because X, Y, Z")
  Risk:   Latency (agent loops), cost

Option 1E — Proactive Alert Agent (NOT feasible now)
  Requires: user accounts + push notifications + real-time crime feed
  Blocked by: no auth, no persistent user storage
```

### Decision 2: How to integrate the agent with the existing API?

```
Option 2A — Inline synchronous (add to POST /routes/recommend response)
  Pros:  Simple — one request, one response
  Cons:  +3-8 s total latency; risky if LLM times out
  When:  Use only if model is very fast (Haiku) or response is small

Option 2B — Separate endpoint (POST /routes/advice)
  Pros:  Route response stays fast; advice fetched on demand
  Cons:  Two frontend requests; slight complexity
  When:  Best default — routes return in 3-5 s, user clicks "Get safety advice"
  ⭐ Recommended

Option 2C — Server-Sent Events (GET /routes/advice/stream)
  Pros:  Tokens stream in real time; feels fast even for long responses
  Cons:  Frontend needs EventSource API; slightly more complex backend
  When:  Best UX for conversational agent (Option 1C)

Option 2D — Background task + polling (POST triggers task, GET polls result)
  Pros:  Non-blocking; works with slow models
  Cons:  Two-step frontend; no push, user must poll
  When:  If Sonnet latency is unacceptable and streaming is not implemented yet

Option 2E — Background task + WebSocket
  Pros:  Push-based, clean UX
  Cons:  WebSocket infrastructure on Azure Container Apps is extra config
  When:  If polling feels janky and SSE is not sufficient
```

### Decision 3: What model to use?

```
Option 3A — Claude Haiku 4.5 (Anthropic)
  Cost:    ~₹150/month at 100 calls/day
  Speed:   Very fast (~0.5 s first token, ~2 s complete)
  Quality: Good for structured summarisation; adequate for safety advisories
  SDK:     anthropic Python SDK (not in requirements.txt yet)
  ⭐ Recommended for production budget

Option 3B — Claude Sonnet 4.6 (Anthropic)
  Cost:    ~₹1,800/month at 100 calls/day — over ₹500 budget
  Speed:   Moderate (~1 s first token, ~5 s complete)
  Quality: Best reasoning, best tool use, best for conversational agent
  Use:     Demo, low-traffic portfolio, or conversational agent (1C)

Option 3C — GPT-4o-mini (OpenAI)
  Cost:    ~₹113/month at 100 calls/day
  Speed:   Fast
  Quality: Comparable to Haiku for summarisation
  Con:     Adds OpenAI dependency; Anthropic already used in sister repo

Option 3D — Local model via Ollama (e.g. Mistral 7B, Llama 3.2)
  Cost:    $0 per call; but requires a VM (~$10-20/month for a small Azure VM)
  Speed:   Moderate on CPU (~3-8 s)
  Quality: Lower than Haiku for structured safety reasoning
  Con:     Ops burden; not free when counting VM cost

Option 3E — No LLM (enhanced retrieval only)
  Cost:    $0
  Speed:   Existing latency
  Quality: Results are already semantically ranked; no natural language generation
  Use:     If budget is a hard constraint
```

### Decision 4: What new data would the agent need, and how to collect it?

```
Option 4A — Ephemeral only (no new storage)
  Data:  Route geometry + risk band + nearby incidents + situation text
  Store: Nothing new — all passed at request time
  Pros:  Zero infra change, no privacy concern, DPDP Act safe
  Cons:  No learning over time, no preference improvement
  ⭐ Only option available without user accounts

Option 4B — Anonymous session preferences (browser-side)
  Data:  User's typical answers (travelling_with, transport_mode, destination_type)
  Store: localStorage — persist answers across sessions
  Pros:  No backend change; no auth; pre-fills questionnaire next visit
  Cons:  Lost if browser cleared; not available on different devices

Option 4C — Anonymous aggregate feedback (server-side)
  Data:  Which risk band routes users actually select (Route 1 vs Route 2 vs 3)
  Store: New table/container — just {route_rank_selected, risk_band_selected, time_band}
  Pros:  Powers future reranking improvements; fully anonymous
  Cons:  Minor DPDP Act consideration; new storage needed
  When:  Useful if aggregate signal is enough to tune BAND_LOW/HIGH_THRESHOLD

Option 4D — User accounts (full personalisation)
  Data:  Search history, saved routes, feedback, notification preferences
  Store: New Cosmos DB container or PostgreSQL
  Pros:  Full personalisation pipeline becomes feasible
  Cons:  Auth (Google SSO), DPDP Act compliance, significant complexity
  When:  v2 only; out of scope per CLAUDE.md
```

### Decision 5: RabbitMQ vs. alternatives for async agent calls?

```
Option 5A — FastAPI BackgroundTasks (zero new infra)
  Pattern: Request returns immediately; task runs in background; client polls /status
  Pros:  Already in codebase (hot-reload loop); no new dependencies
  Cons:  Tasks lost on container restart; no retry; no durability
  When:  Acceptable for fire-and-forget advisory generation

Option 5B — Server-Sent Events (zero new infra)
  Pattern: Client opens EventSource; backend streams LLM tokens
  Pros:  Real-time streaming; feels responsive; built into FastAPI
  Cons:  Connection held open for duration of LLM call
  When:  Best for streaming safety advisory (Options 1A, 1C)
  ⭐ Recommended if streaming is desired

Option 5C — Redis + Celery (low infra addition)
  Pattern: Request enqueues task; Celery worker picks it up; result cached in Redis
  Pros:  Durable, retryable, inspectable
  Cons:  Two new services (Redis + Celery); Docker Compose update needed
  When:  If tasks must survive container restarts and volume justifies it

Option 5D — Azure Service Bus (Azure ecosystem)
  Pattern: Backend publishes message; worker subscribes
  Pros:  Managed, durable, integrates with Azure infra already in use
  Cons:  Cost (~₹400/month for Basic tier); new Azure resource
  When:  If workload scales beyond Container Apps free tier

Option 5E — RabbitMQ (maximum flexibility)
  Pattern: AMQP broker; multiple consumers; dead-letter queues
  Pros:  Most powerful; fan-out, routing, priority queues
  Cons:  Most complex; new service to operate and monitor
  When:  Only when multiple consumers need to react to the same event
         (e.g. crime record → re-score routes AND send push notification)
  Verdict: PREMATURE for current requirements
```

---

## 5. Recommended Starting Point (Flagged, Not Decided)

Based on the codebase constraints (no auth, free tier budget ~₹500/month, stateless architecture), the highest value/effort ratio path is:

**Step 1 — Safety Advisory Agent (Option 1A + 2B + 3A)**

A new endpoint `POST /routes/advice` that takes:
```json
{
  "route": { "geometry": ..., "risk_band": "High", "duration_sec": 1020 },
  "incidents": [ { "crime_macro": "Robbery", "summary": "...", "location_exact": "..." } ],
  "situation": "Woman travelling Alone by Walking arriving at Isolated or poorly lit road"
}
```

And returns:
```json
{
  "advice": "This route passes through areas with recent robbery incidents near Connaught Place...",
  "risk_level": "High",
  "key_concerns": ["robbery hotspot near CP Metro", "poorly lit stretch near..."],
  "timing_note": "Consider travelling before 8pm — nighttime multiplier is 2.5×"
}
```

Powered by Claude Haiku 4.5 with a tight system prompt. Streamed via SSE for good UX. No new infrastructure needed.

**Step 2 — Pre-fill questionnaire from localStorage (Option 4B)**

Persist the user's last questionnaire answers in localStorage. On next visit, answers are pre-selected. Zero backend change, zero privacy concern.

**Step 3 (later) — Conversational planner (Option 1C)**

Replace the origin/destination text inputs + 3-question form with a Claude agent chat interface. Requires streaming UI and more careful prompt engineering.

---

## 6. What Would Need to Be Built (By Option)

### For Option 1A (Safety Advisory, recommended)

| Component | Change | Effort |
|---|---|---|
| `backend/requirements.txt` | Add `anthropic` SDK | Trivial |
| `backend/app/config.py` | Add `ANTHROPIC_API_KEY: str = ""` | Trivial |
| `backend/app/services/advice_service.py` | New: build prompt, call Claude Haiku, return structured advice | Small |
| `backend/app/routers/advice.py` | New: `POST /routes/advice` — validate request, call service, return or stream | Small |
| `backend/app/main.py` | Register `advice_router` | Trivial |
| `frontend/src/api/client.js` | Add `getRouteAdvice()` function | Trivial |
| `frontend/src/components/RouteResults.jsx` | Add "Get safety advice" button, render advisory card | Small |
| Azure env vars | `ANTHROPIC_API_KEY` | Trivial |

### For Option 1C (Conversational planner)

| Component | Change | Effort |
|---|---|---|
| All of 1A above | — | — |
| `backend/app/routers/chat.py` | New: `POST /chat` with tool definitions (geocode, recommend, search) | Medium |
| `backend/app/services/chat_service.py` | New: Claude agent loop with tools | Medium |
| SSE support | FastAPI `StreamingResponse` + EventSource on frontend | Small |
| `frontend/src/components/ChatPanel.jsx` | New: replace form with chat input, stream display | Medium |

### For RabbitMQ (only if async delivery is needed)

| Component | Change | Effort |
|---|---|---|
| `docker-compose.yml` | Add `rabbitmq:3.13-management` service | Trivial |
| `backend/requirements.txt` | Add `aio-pika` or `kombu` | Trivial |
| `backend/app/services/queue.py` | New: publish/consume helpers | Small |
| Worker process | New Docker service or FastAPI background consumer | Medium |
| Azure | Deploy second Container App (worker) or add sidecar | Medium |

---

## Summary Table

| Option | Feasible Now? | Budget Impact | New Infra | Effort | Value |
|---|---|---|---|---|---|
| 1A Safety advisory (Haiku, SSE) | ✅ Yes | +~₹150/mo | None | Low | High |
| 1B Query expansion | ✅ Yes | Negligible | None | Very Low | Marginal |
| 1C Conversational planner (Haiku) | ✅ Yes | +~₹150/mo | None | Medium | Very High |
| 1C Conversational planner (Sonnet) | ⚠️ Over budget | +~₹1,800/mo | None | Medium | Very High |
| 1D Autonomous evaluator | ⚠️ Over budget | High | None | Medium | High |
| 1E Proactive alerts | ❌ Blocked | — | Queue + auth | High | High (if auth exists) |
| RabbitMQ | ❌ Premature | +₹400/mo (Azure SB) | Yes | High | None yet |
| User accounts | ❌ Out of scope | Significant | Auth + DB | Very High | Enables everything |
