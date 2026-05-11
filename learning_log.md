# Learning Log

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
