### Executive Summary

Phase 7 is not ready for implementation. The plan's central edge-risk lookup is not reliable: Stage 2 downloads a separately timed OSMnx/Overpass graph rather than reading the same PBF as GraphHopper, OSMnx and GraphHopper segment roads differently, and Shapely's length-weighted line centroid is not the same calculation as the Java average of geometry vertices. More immediately, the proposed `CrimeWeighting.java`, `DefaultWeightingFactory` patch, and `config.yml` do not compile or start against GraphHopper 9.1. The Python spatial-join pseudocode also fails with OSMnx's `(u, v, key)` MultiIndex. These issues must be redesigned and specified before Stage 1 begins.

### Check results

**Check 1 - Interface contract between Stage 2 and Stage 4**  
Status: FAIL

- **1a - Coordinate order: PASS.** In EPSG:4326, Shapely stores coordinates as `x=longitude, y=latitude`, so Python's `f"{centroid.y:.4f},{centroid.x:.4f}"` produces `lat,lng`. GraphHopper `PointList.getLat()`/`getLon()` and Java's `String.format("%.4f,%.4f", midLat, midLon)` use the same order.
- **1b - Four-decimal tolerance: FAIL.** Four decimal places is a rounding bucket, not an 11 m matching tolerance: two points less than 11 m apart can round to adjacent keys, while two points farther apart diagonally can share a key. More importantly, the plan's premise that both tools read the same PBF is false. Stage 2 downloads current OSM data from Overpass; GraphHopper reads the manually downloaded Delhi PBF, so timestamps and coverage can differ. OSMnx's simplified edge can also span several GraphHopper edges, making corresponding midpoints tens or hundreds of metres apart.
- **1c - Midpoint calculation: FAIL.** For a two-point straight segment, averaging endpoints equals its line centroid. For a curved or unevenly digitized line, averaging vertices is not Shapely's centroid. Shapely's line centroid is weighted by segment length; the Java calculation weights every stored vertex equally. Fifteen clustered vertices near one end can move the Java result far from the length midpoint.
- **Additional silent-miss risk:** `String.format` uses the JVM default locale. A locale that uses decimal commas produces incompatible keys. The Java side must use `String.format(Locale.ROOT, ...)`.

**Recommendation:** Do not use independently generated rounded centroid strings as the primary contract. Generate the risk mapping from GraphHopper's imported graph, or import a stable edge risk encoded value using an explicit OSM-way/segment mapping. At minimum, use the same pinned PBF, the same segmentation, a length-interpolated midpoint on both sides, nearest-neighbour lookup with a measured tolerance, and runtime match-rate telemetry.

**Check 2 - OSMnx graph vs GraphHopper graph consistency**  
Status: FAIL

- **2a - Segment consistency: FAIL.** `graph_from_bbox(..., network_type="drive")` does not guarantee the same road set as GraphHopper's car profile. OSMnx removes non-endpoint pass-through nodes and consolidates them into geometry-bearing edges. GraphHopper retains junction tower nodes and geometry pillar nodes according to its own import rules. One OSMnx edge can therefore correspond to multiple GraphHopper edges. The 150 m crime buffer only determines which OSMnx edges receive risk; it does nothing to make GraphHopper midpoint keys match those OSMnx edges.
- The planned OSMnx bounding box includes Gurgaon/Noida portions, while `delhi-latest.osm.pbf` covers NCT Delhi only. Stage 2 can emit risk keys for roads GraphHopper does not contain.
- **2b - Detection: FAIL.** The plan checks the percentage of OSMnx edges that were assigned crimes, not the percentage of GraphHopper edge lookups that match JSON keys. It has no runtime counters for matched lookups, unmatched lookups, unique keys hit, or risk-weighted route edges. Systematic misses will silently degrade `balanced` and `safest` toward the base profile.

**Recommendation:** Add startup validation that scans GraphHopper base edges and reports exact/nearest match rates, collision counts, and non-zero-risk match rates. Refuse startup or disable crime-aware profiles below an explicit threshold.

**Check 3 - CrimeWeighting.java GraphHopper API compatibility**  
Status: FAIL

Verification used the GraphHopper 9.1 release tag at commit `73e6b7cc3ca163ce0b53692f7cd732dba170bfce`.

- **3a - `AbstractWeighting` constructor: FAIL.** GraphHopper 9.1 has no `FlagEncoder` constructor. Its constructor is `AbstractWeighting(BooleanEncodedValue accessEnc, DecimalEncodedValue speedEnc, TurnCostProvider turnCostProvider)`. The planned `super(encoder, new PMap())` cannot compile.
- **3b - Time method and edge-weight signature: FAIL.** GraphHopper 9.1 exposes `calcEdgeMillis(EdgeIteratorState, boolean)`, not `calcMillis(edge, reverse, false)`. `Weighting.calcEdgeWeight` takes two arguments, not the planned three arguments `(edge, reverse, prevOrNextEdgeId)`.
- **3c - `FetchMode.ALL`: PASS.** `FetchMode.ALL` exists in GraphHopper 9.1.
- **3d - Factory patch: FAIL.** `DefaultWeightingFactory` is in package `com.graphhopper.routing`, not `com.graphhopper.routing.weighting`. It uses an if/else chain and has no `registerWeighting()` API, so patching is necessary, but the plan's switch-case patch cannot be inserted as written. The factory must obtain `car_access`, `car_average_speed`, and the `TurnCostProvider` from its existing `EncodingManager`/factory flow and pass them to the new weighting.
- GraphHopper 9.1 does not support a built-in `fastest` weighting in `DefaultWeightingFactory`; it throws and instructs callers to use `weighting: custom`. The planned `fastest` profile will fail.
- GraphHopper 9.1 `Profile` explicitly rejects the `vehicle` key. All three planned profile entries contain `vehicle: car`, so configuration parsing will fail.
- The planned `server.application_connectors` block is incorrectly nested under `graphhopper:` and uses `bindHost`; GraphHopper's example uses a top-level `server:` block and `bind_host`.
- The plan assumes `org.json` is already a GraphHopper core dependency, but no `org.json` use/dependency was found in the 9.1 source. Use GraphHopper/Jackson dependencies already available or add an explicit dependency.
- The weighting-unit design is inconsistent with GraphHopper 9.1. `calcEdgeWeight()` conventionally returns seconds-based weight, while `calcEdgeMillis()` returns physical travel time in milliseconds. The plan returns milliseconds as weight and documents `calcMinWeightPerDistance()` as `1/maxSpeed`, which is not in matching units.
- **3e - Version pin: WARN.** `9.1` is an immutable release tag in the inspected repository, not a moving release branch. `--branch 9.1` currently resolves to the exact commit above. Pinning the SHA is still preferable for reproducibility and protection against a moved tag.

**Recommendation:** Rewrite Stage 3/4 against the actual 9.1 interfaces and provide a complete compiling factory patch, complete configuration, unit-consistent weighting, and a Maven compile test before proceeding.

Sources:
- GraphHopper 9.1 source/tag: https://github.com/graphhopper/graphhopper/tree/9.1
- GraphHopper routing profiles: https://github.com/graphhopper/graphhopper/blob/9.1/docs/core/profiles.md

**Check 4 - Docker and volume mount consistency**  
Status: WARN

- **4a - Edge-risk path: PASS, conditional on fixing Stage 3/4.** Compose mounts `./ml/artifacts/edge_risk.json` at `/data/edge_risk.json`; the proposed profile hint uses `/data/edge_risk.json`; the proposed constructor accepts that path. The path strings agree, but the current factory/config design cannot actually pass the value successfully.
- **4b - PBF path: PASS.** Stage 1's `graphhopper/data/delhi-ncr-latest.osm.pbf` matches the compose bind source and `/data/delhi-ncr-latest.osm.pbf` matches `datareader.file`.
- **4c - Graph cache after JSON changes: WARN.** If routing remains flexible and `CrimeWeighting` reads JSON at startup, restarting GraphHopper is sufficient and the base graph need not be rebuilt. If CH/LM preparation is later enabled for a crime-aware profile, its prepared weights become stale and the graph/preparation must be rebuilt.
- **4d - Rebuild/restart behavior: WARN.** A `config.yml` change requires image rebuild because it is copied into the image. A JSON content change requires a GraphHopper restart because the file is loaded only in the constructor. Merely editing the bind-mounted JSON while the container runs does nothing. A PBF or graph-encoded-value change requires deleting/rebuilding the graph cache.
- The plan does not add a health check to the GraphHopper compose service, so `depends_on` with readiness cannot be added later without more work.

**Check 5 - Python pipeline feasibility**  
Status: FAIL

- **5a - Bounding box: PASS with a minor caveat.** The actual snapshot's KDE pool contains 4,655 points. Exactly 4,601 are inside the proposed bbox and 54 are outside (1.16%): 10 north, 10 south, 33 east, and 5 west. This does not meaningfully reduce Delhi coverage. However, the bbox still contains roads outside the Delhi-only PBF, creating unusable JSON keys.
- **5b - CRS handling: PASS.** Section 3c explicitly reprojects both `edges` and `crime_gdf` to EPSG:32643 before buffering and joining.
- **5c - `build_kde_pool` import: PASS with environment caveat.** `build_kde_pool` and `find_latest_snapshot` are top-level importable functions in `ml/train_kde.py`. Importing that module also imports `mlflow`, so the script fails if the full ML requirements are not installed; this is expected if `ml/requirements.txt` is installed.
- **5d - OSMnx API/version: FAIL.** The planned call works, with deprecation warnings, on OSMnx 1.9.4. The proposed requirement `osmnx>=1.9.0` currently permits OSMnx 2.1.0, whose signature is `graph_from_bbox(bbox, *, ...)`; the planned `north=`, `south=`, `east=`, `west=` call raises `TypeError`. Pin a tested version, preferably `osmnx==1.9.4`, or update the code and pin OSMnx 2.x.
- **5e - Edge iteration/geometry: PASS.** `graph_to_gdfs(G, nodes=True, edges=True)` returns edge rows indexed by `(u, v, key)`. With default `fill_edge_geometry=True`, every output edge gets geometry, including unsimplified two-node edges.
- **Spatial-join pseudocode: FAIL.** GeoPandas 0.14.4 expands a right-side MultiIndex into `index_right0`, `index_right1`, and `index_right2`. The plan groups on a single `index_right` column and expects it to contain `(u, v, key)`, so the shown accumulation code fails. Reset/rename the edge index to explicit `u`, `v`, `key` columns before `sjoin`, then group by all three columns.
- **Input reproducibility: FAIL.** Stage 2 uses Overpass instead of the Stage 1 PBF, so it cannot guarantee identical source data or offline reproducibility.

Sources:
- OSMnx 1.9.4 source: https://github.com/gboeing/osmnx/tree/v1.9.4
- OSMnx current docs: https://osmnx.readthedocs.io/
- GeoPandas spatial join docs: https://geopandas.org/en/stable/docs/reference/api/geopandas.sjoin.html

**Check 6 - Backend integration consistency**  
Status: PASS

- **6a - Endpoint: PASS.** Self-hosted GraphHopper uses `/route`; `/api/1/route` is the hosted GraphHopper Directions API prefix.
- **6b - Geometry response: PASS.** With `points_encoded=false`, GraphHopper returns `paths[i].points` as a GeoJSON LineString object containing `coordinates`. The frontend passes `route.geometry` directly to a GeoJSON `Source`, so assigning `geometry = path["points"]` preserves the contract.
- **6c - Pydantic v2 default: PASS.** `ORS_API_KEY: str = ""` allows the environment variable to be absent. `Optional[str]` is not required because the default is a valid string, not `None`.
- **6d - Settings import: PASS.** `backend/app/routers/routes.py` already imports `Settings` and instantiates module-level `settings`.

Source: https://docs.graphhopper.com/openapi/routing

**Check 7 - Cloudflare Tunnel feasibility**  
Status: FAIL

- **7a - Stable hostname: FAIL.** A free Cloudflare plan is sufficient for a named tunnel, but publishing a stable public hostname requires a domain added to Cloudflare and delegated to Cloudflare DNS. The tunnel UUID's `cfargotunnel.com` hostname is a routing target, not a user-selectable public subdomain. A free quick tunnel needs no domain but receives a random `trycloudflare.com` hostname and is intended only for testing. The plan omits the registered-domain cost/setup dependency and incorrectly suggests a default public `cfargotunnel.com` subdomain.
- **7b - Windows service config: FAIL.** The service runs as `LocalSystem`; Cloudflare's Windows instructions place/copy configuration and credentials under `C:\Windows\System32\config\systemprofile\.cloudflared\` or explicitly pass `--config`. The plan's claim that the service reads `C:\Users\Sandip\.cloudflared\config.yml` is not reliable and will commonly fail.
- **7c - Latency: WARN.** The plan's 30-80 ms tunnel overhead is understated. A Delhi browser calls Azure East Asia, Azure calls through Cloudflare back to Delhi, and the response reverses both legs. A realistic network-only total is roughly 200-500 ms before GraphHopper computation, backend post-scoring, Qdrant retrieval, and cold starts. A warm portfolio-demo request may still be acceptable, but multi-second requests are plausible and must be measured.

Sources:
- Named local tunnel prerequisites: https://developers.cloudflare.com/tunnel/advanced/local-management/create-local-tunnel/
- Windows service setup: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/local-management/as-a-service/windows/
- Quick tunnels: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/

**Check 8 - Missing pieces and gaps**  
Status: FAIL

- **8a - Factory patch: FAIL.** The plan gives only a small switch-case snippet, not a complete patch. GraphHopper 9.1 uses an if/else factory in a different package and requires encoded values plus a turn-cost provider. This is a Stage 3/4 compilation blocker.
- **8b - Profile selection: WARN.** `RouteRequest` has no profile field, and the plan hardcodes `settings.SAFETY_PROFILE` for every request. Users cannot compare `fastest`, `balanced`, and `safest`, despite the verification plan and portfolio narrative depending on that comparison. Add a validated per-request profile or explicitly narrow the demo to one profile.
- **8c - Weekly refresh: FAIL.** `.github/workflows/retrain-weekly.yml` retrains KDE/LGB models, generates heatmaps, updates a GitHub release, and triggers a backend image build. It does not run `build_edge_risk.py`, publish `edge_risk.json`, transfer it to the local GraphHopper host, or restart GraphHopper. Edge scores will become stale after the first build.
- **8d - Startup race: FAIL.** Compose has no GraphHopper health check, the backend has no readiness dependency, and the proposed backend startup health check only warns once. Requests during first import/preprocessing will return failures. Add GraphHopper health checking, backend retry/backoff, and a clear unavailable/readiness response.
- **8e - File bind mount: FAIL.** If host-side `ml/artifacts/edge_risk.json` does not exist before `docker compose up`, Docker can create a directory at that path and GraphHopper will fail confusingly. Add a preflight script/check and make startup fail with a precise error before compose starts.
- No implementation is provided for GraphHopper lookup match-rate metrics, collision detection, or startup quality gates.
- No exact tests are specified for the custom weighting, factory dispatch, profile configuration, missing/invalid JSON, locale-independent keys, or route divergence.
- Stage 1 downloads an NCT-only extract while project scope and route examples use Delhi-NCR. The accepted geographic limitation is not enforced in geocoding/backend validation, so requests to Gurgaon/Noida can still reach GraphHopper and fail.

**Check 9 - End-to-end data flow integrity**  
Status: FAIL

1. **Parquet row -> KDE pool filter:** DataFrame rows are filtered by `is_delhi_crime`, non-null latitude, non-historical status, and eligible category. Loss is intentional, but `is_delhi_crime` is based on broad location text and can include points outside the routable NCT graph. The plan handles filtering but not graph-coverage validation.
2. **KDE pool -> `kde.dataset`:** `train_kde.py` stacks `[lat, lng]`, and the inspected robbery artifact is `(2, 827)` with row 0 latitude and row 1 longitude. This is correct. `build_edge_risk.py` does not actually use `.dataset` as its crime-row source; it reloads the pool from Parquet, so artifact/snapshot generations can drift if they are not produced together. The plan does not validate matching snapshot/model versions.
3. **Pool/model -> `build_edge_risk.py`:** Model artifacts provide KDE density; Parquet provides category/date. Missing models, stale artifacts, missing `mlflow` dependency, or category mismatch can fail. The plan handles model loading conceptually but has no generation/version manifest.
4. **Crime point -> spatial join to OSMnx edge:** Both layers are correctly projected and a 150 m buffer is applied. Points outside the bbox or buffers are dropped. The shown code fails on the edge MultiIndex because it expects `index_right`; the plan does not handle this.
5. **Joined rows -> raw edge score:** Density, female weight, and recency are accumulated. The same crime can contribute to many dense parallel edges by design. KDE recency is applied again, so temporal weighting is doubled. The plan acknowledges the latter but has no calibration or sanity bounds.
6. **Raw score -> normalized score:** Dividing by the global maximum makes every edge score depend on one outlier and makes lambda calibration unstable across retrains. The plan provides no percentile clipping, zero-max guard, or distribution validation.
7. **OSMnx edge -> JSON key:** The Python coordinate order is correct, but centroid keys can collide and are generated from a different graph source/segmentation than GraphHopper. Later entries overwrite earlier entries. The plan understates collision and mismatch risk.
8. **JSON -> GraphHopper HashMap:** The proposed file path is consistent and missing-file startup failure is intended. Invalid JSON/type/range handling, duplicate-key detection, locale-independent formatting, and match-rate validation are absent. The proposed parser dependency is also unverified.
9. **HashMap -> `calcEdgeWeight()`:** The proposed Java class does not compile against 9.1. Even after compilation fixes, most risks can become zero because exact rounded keys miss. The plan does not detect this.
10. **Weight -> routing algorithm avoids edge:** The intended additive cost can steer routing only if matches occur and units are correct. The proposed profiles/configuration do not start on 9.1, and the weighting/heuristic units are inconsistent.
11. **GraphHopper route -> `routing.py`:** `/route`, `paths`, time, distance, and unencoded GeoJSON geometry are compatible. Error wording and tests must be changed from ORS-specific behavior; the plan mentions the rewrite but does not specify all failure mappings.
12. **Route -> KDE post-scoring:** Existing waypoint sampling consumes GeoJSON `[lng, lat]` and emits `(lat, lng)`, so the coordinate order remains correct. A crime-aware GraphHopper route is then independently scored by KDE; this can rank alternatives differently from GraphHopper's edge-risk objective. The plan does not define how to explain or resolve that disagreement.
13. **Backend response -> frontend render:** `RouteOption.geometry` is a dict and `MapView.jsx` accepts a GeoJSON source, so rendering is compatible. Users cannot identify which safety profile generated a route because profile is absent from request/response/UI.

**Check 10 - Audit findings from the plan itself**  
Status: WARN

- **Finding 1 - Deduplication absent: ACCURATE.** `routing.py` contains neither `_route_fingerprint` nor `_deduplicate_routes`. Adding them in the rewrite can resolve this, but the plan does not specify their algorithm, thresholds, call site, or tests. Deduplication is not a GraphHopper integration prerequisite unless duplicate alternatives are observed.
- **Finding 2 - `ORS_API_KEY` required: ACCURATE.** `backend/app/config.py` declares `ORS_API_KEY: str` without a default. Changing it to `ORS_API_KEY: str = ""` is correct in Pydantic v2 and does not require `Optional`. Existing ORS fallback code must explicitly reject an empty key if that fallback remains callable.
- **Finding 3 - Cache key hardcodes `driving-car`: ACCURATE.** `routes.py` hardcodes the string and already has module-level `settings`. Replacing it with `settings.SAFETY_PROFILE` resolves the single-profile design. If per-request profile selection is added, the cache key must use the request's validated profile instead.
- **Finding 4 - No top-level named volumes section: ACCURATE.** Current compose uses bind/anonymous volumes only. Adding `graphhopper_graph:` at top level correctly supports the proposed named volume. Cache invalidation rules must also be documented.
- **Dependency note:** Findings 2 and 3 depend on the backend routing switch. Finding 4 depends on the GraphHopper service. Finding 1 is independent.

### Critical blockers

1. The rounded-centroid contract cannot reliably map OSMnx edges to GraphHopper edges. Fix it by deriving risk data from the same pinned PBF and GraphHopper segmentation, or by implementing an explicit stable edge mapping with measured lookup quality.
2. The proposed `CrimeWeighting.java` does not compile against GraphHopper 9.1. Rewrite it for the actual encoded-value constructor, two-argument weighting methods, correct time method, correct units, and complete required overrides.
3. The proposed `DefaultWeightingFactory` patch is structurally wrong and incomplete. Provide and compile-test the full GraphHopper 9.1 factory change in `com.graphhopper.routing`.
4. The proposed GraphHopper `config.yml` is invalid for 9.1 because it uses rejected `vehicle` fields, unsupported `fastest` weighting, incorrect server nesting, and the wrong bind-host key. Replace it with a configuration validated by starting GraphHopper 9.1.
5. The Stage 2 spatial-join pseudocode fails because OSMnx edge MultiIndex levels become separate right-index columns. Reset the edge index to explicit `u`, `v`, and `key` columns before joining.
6. `osmnx>=1.9.0` allows OSMnx 2.x, but the planned `graph_from_bbox` call is 1.9-style. Pin a tested OSMnx version or update the code for 2.x.
7. The weekly retrain workflow never rebuilds or deploys `edge_risk.json`. Add generation, artifact publication/transfer, validation, and GraphHopper restart steps.
8. The Cloudflare stable-hostname plan is not deployable without a Cloudflare-managed domain, and the Windows service config path is wrong. Choose and document a valid domain/tunnel/service setup.
9. The plan has no GraphHopper-edge lookup telemetry or quality gate, so crime-aware routing can silently become fastest routing. Add startup and runtime match-rate/collision metrics with failure thresholds.

### Warnings

1. Only 54 of 4,655 KDE-pool points are outside the OSMnx bbox, but the bbox and Delhi-only PBF still have different road coverage.
2. Updating bind-mounted `edge_risk.json` requires a GraphHopper restart; it would require graph/preparation rebuild if CH/LM crime-aware profiles are introduced.
3. Per-request profile selection is absent, weakening the planned fastest-vs-safest demo.
4. The tunnel latency estimate is optimistic; measure warm, cold, and failure-path latency from Delhi.
5. Normalizing by one maximum raw score makes lambda behavior unstable between retrains.
6. Route post-scoring can rank alternatives differently from the GraphHopper crime-aware objective.
7. Deduplication is identified but underspecified.
8. Pin GraphHopper to commit `73e6b7cc3ca163ce0b53692f7cd732dba170bfce` for fully reproducible builds.

### Needs-research items

1. **Graph mapping experiment - blocking before Stage 2:** Using one pinned Delhi PBF, measure how many GraphHopper base edges can be matched to OSMnx edges under candidate mappings, including collisions and non-zero-risk coverage. Do not choose a mapping method without these results.
2. **GraphHopper extension spike - blocking before Stage 3:** Build a minimal custom weighting and complete factory patch against GraphHopper 9.1, then start the server with one valid custom profile and route request.
3. **Weighting/preparation behavior - blocking before enabling CH/LM:** Confirm whether the final custom weighting will run only in flexible mode or be prepared with CH/LM, and define graph-cache invalidation accordingly.
4. **Cloudflare deployment test - blocking before Stage 6:** Confirm ownership of a Cloudflare-managed domain, install the Windows service using the LocalSystem config path or explicit `--config`, and measure Azure-East-Asia-to-Delhi round-trip latency.
5. **Operational refresh design - blocking before weekly automation:** Decide how GitHub Actions delivers the newly generated edge-risk artifact to the local Windows GraphHopper host and securely triggers validation/restart.

### Verdict

NOT READY - the core graph-to-edge contract, GraphHopper 9.1 implementation, Python join, and production tunnel/refresh paths contain confirmed blockers that must be redesigned before implementation.
