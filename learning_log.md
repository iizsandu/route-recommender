# Learning Log

---

## P0-4 — Deploy Frontend to Vercel (2026-05-11)

### Concept MCQ results (5 questions)
- Q1 B ✓ — Vite strips non-`VITE_*` vars at build time; browser gets `undefined`
- Q2 C ✓ — Vercel atomic deploy: failed build cancels, old build stays live
- Q3 C ✗ → correct: B — `npm run build` checks compilation, NOT runtime API reachability
- Q4 A ✗ → correct: B — mixed content (HTTPS frontend + HTTP backend) blocks fetch regardless of CORS
- Q5 B ✓ — Vercel env var scopes (Production / Preview / Development) handle per-environment URLs

### Key ideas to retain
- `VITE_API_BASE_URL` must exist at *build time* — baked into the bundle, not read at runtime. A missing var means `undefined` in the browser.
- Local dev uses the vite.config.js proxy (`/api/health` → `localhost:8000/health`), so `VITE_API_BASE_URL` can be unset locally.
- The GitHub Actions CI workflow only proves the bundle compiles. Vercel's own build is what deploys.

### Skipped
- Step 2 guided walkthrough and Step 3 post-write quiz skipped by user request (protocol break confirmed).

---

## P0-2 — Cosmos DB Read-Only Client (2026-05-06)

### 3 things understood that weren't before

1. **Why async client construction is deferred to `connect()`** — `CosmosClient` opens an `aiohttp` session on instantiation, which requires a live event loop. `__init__` is synchronous; the event loop may not exist yet. `connect()` is called from FastAPI's `lifespan`, which runs inside the loop.

2. **Why `frozenset` for `_COSMOS_INTERNAL_FIELDS`** — `in` on a `frozenset` is O(1) average (hash lookup) vs O(n) for a list. Small win here (5 fields), but the pattern is correct for any membership test that runs per-document.

3. **Why `_ts` requires `int(since.timestamp())` not a string** — Cosmos stores `_ts` as a Unix epoch integer. String comparison against an integer field either fails silently or errors. Python's `datetime.timestamp()` returns a float; `int()` cast aligns with Cosmos's integer type.

### 1 thing not yet fully grasped

- **`enable_cross_partition_query=True`** — understood *that* it's required for `SELECT *`, but not yet clear on what "cross-partition" means in terms of Cosmos's physical storage model (how data is partitioned and why fan-out is expensive).

### 1 question answered wrong (must get right next time)

- **Chunk 3 prediction:** "What should the happy-path test assert?" — answered C ("verify mock was called") instead of B ("verify business fields present + metadata absent"). Lesson: test *behaviour* (output shape and content), not *implementation* (whether a mock was invoked).

### Skipped

- Step 3 Round A (conceptual MCQs) and Round B (FAANG interview questions) were skipped by user choice. Revisit Round A Q2 (naive datetime timezone assumption) and Q3 (correct RBAC role) next session.
