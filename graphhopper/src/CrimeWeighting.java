/*
 * Crime-aware edge weighting for GraphHopper 9.1.
 * Extends AbstractWeighting so we inherit calcEdgeMillis() (pure travel time)
 * and only override the cost function.
 */
package com.graphhopper.routing.weighting;

import com.graphhopper.routing.ev.BooleanEncodedValue;
import com.graphhopper.routing.ev.DecimalEncodedValue;
import com.graphhopper.routing.util.AllEdgesIterator;
import com.graphhopper.routing.weighting.TurnCostProvider;
import com.graphhopper.util.EdgeIteratorState;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.File;
import java.io.IOException;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

public class CrimeWeighting extends AbstractWeighting {

    private static final String NAME = "crime_aware";

    // Keyed by GH integer edge ID (Long). O(1) lookup, no string formatting,
    // no locale dependency, no centroid mismatch — the edge IDs came from this
    // exact graph via EdgeExporter.
    private final Map<Long, Double> edgeRiskScores;
    private final double lambda;

    // Pre-computed once so calcMinWeightPerDistance() is a trivial division.
    private final double maxSpeedMps;

    // Written on every calcEdgeWeight call; read by validateMatchRate().
    // volatile: multiple routing threads write, startup thread reads.
    volatile long lookupCount  = 0;
    volatile long matchCount   = 0;
    volatile long nonZeroCount = 0;

    public CrimeWeighting(BooleanEncodedValue accessEnc,
                          DecimalEncodedValue avgSpeedEnc,
                          TurnCostProvider turnCostProvider,
                          double lambda,
                          String edgeRiskPath) {
        // AbstractWeighting(BooleanEncodedValue, DecimalEncodedValue, TurnCostProvider)
        // — the only constructor in GH 9.1. Stores the encoded values and provides
        // a default calcEdgeMillis() we can delegate to.
        super(accessEnc, avgSpeedEnc, turnCostProvider);
        this.lambda      = lambda;
        // getMaxOrMaxStorableDecimal() → max speed in km/h. /3.6 → m/s.
        this.maxSpeedMps = avgSpeedEnc.getMaxOrMaxStorableDecimal() / 3.6;
        this.edgeRiskScores = new HashMap<>();

        // ── Load edge_risk.json ───────────────────────────────────────────
        if (!new File(edgeRiskPath).exists()) {
            // Graceful degradation: empty map → all lookups return 0.0 →
            // crime_aware routes identically to fastest until JSON is provided.
            System.err.println("[CrimeWeighting] WARNING: " + edgeRiskPath
                + " not found. Routing as fastest until JSON is provided.");
            return;
        }

        try {
            ObjectMapper mapper = new ObjectMapper();
            JsonNode root   = mapper.readTree(new File(edgeRiskPath));
            JsonNode scores = root.get("edge_scores");

            JsonNode meta = root.get("metadata");
            if (meta != null) {
                System.out.printf("[CrimeWeighting] Loading edge_risk.json: "
                    + "%d scored edges, generated %s%n",
                    meta.path("n_edges_scored").asLong(),
                    meta.path("generated_at").asText("unknown"));
            }

            Iterator<Map.Entry<String, JsonNode>> fields = scores.fields();
            while (fields.hasNext()) {
                Map.Entry<String, JsonNode> entry = fields.next();
                long   edgeId = Long.parseLong(entry.getKey());
                double score  = entry.getValue().asDouble();
                // Clamp to [0, 1] — out-of-range values signal a normalisation bug.
                if (score < 0 || score > 1.01) {
                    System.err.printf("[CrimeWeighting] WARNING: edge %d score %.4f "
                        + "out of range; clamping%n", edgeId, score);
                    score = Math.max(0.0, Math.min(1.0, score));
                }
                edgeRiskScores.put(edgeId, score);
            }
            System.out.printf("[CrimeWeighting] Loaded %d edge risk scores. Lambda=%.2f%n",
                edgeRiskScores.size(), lambda);

        } catch (IOException e) {
            throw new RuntimeException("[CrimeWeighting] Failed to read "
                + edgeRiskPath + ": " + e.getMessage(), e);
        } catch (NumberFormatException e) {
            throw new RuntimeException("[CrimeWeighting] edge_risk.json has "
                + "non-integer key: " + e.getMessage(), e);
        }
    }

    // ── Core routing method ───────────────────────────────────────────────────
    // Called by A*/Dijkstra for every edge considered during pathfinding.
    // Return value unit: SECONDS. GH 9.1 convention for calcEdgeWeight.
    // Using milliseconds here would break the A* heuristic (calcMinWeightPerDistance
    // returns seconds/metre — a 1000× mismatch would produce wrong routes).
    @Override
    public double calcEdgeWeight(EdgeIteratorState edge, boolean reverse) {
        double travelTimeSec = calcEdgeMillis(edge, reverse) / 1000.0;
        long   edgeId        = edge.getEdge();
        double riskScore     = edgeRiskScores.getOrDefault(edgeId, 0.0);

        lookupCount++;
        if (edgeRiskScores.containsKey(edgeId)) {
            matchCount++;
            if (riskScore > 0) nonZeroCount++;
        }

        // lambda=0.1: 1km max-risk road adds 0.1*1.0*1000 = 100 s ≈ 1.7 min extra
        // lambda=0.3: 1km max-risk road adds 0.3*1.0*1000 = 300 s = 5 min extra
        return travelTimeSec + lambda * riskScore * edge.getDistance();
    }

    // Called to display the ETA to the user. Must NOT include the crime penalty —
    // we want to show realistic travel time, not "penalty-inflated" journey time.
    @Override
    public long calcEdgeMillis(EdgeIteratorState edge, boolean reverse) {
        return super.calcEdgeMillis(edge, reverse);
    }

    // A* lower-bound heuristic: minimum possible weight for a given straight-line
    // distance. Crime penalty is always >= 0, so the lower bound is pure travel
    // time at maximum speed. Unit must match calcEdgeWeight: seconds/metre.
    @Override
    public double calcMinWeightPerDistance() {
        return 1.0 / maxSpeedMps;
    }

    @Override
    public String getName() {
        // This string must match the "weighting: crime_aware" value in config.yml
        // and the "crime_aware".equals(weightingStr) check in DefaultWeightingFactory.
        return NAME;
    }

    // ── Startup validator ─────────────────────────────────────────────────────
    // Called by DefaultWeightingFactory after construction.
    // Scans every base edge to measure what fraction appears in edge_risk.json.
    // A low rate means the JSON was built from a different graph version.
    public void validateMatchRate(AllEdgesIterator baseEdges, double minMatchRate) {
        // AllEdgesIterator is a cursor (like a ResultSet): call .next() to advance.
        // It does NOT implement Iterable — for-each loops don't work on it.
        long total = 0, matched = 0;
        while (baseEdges.next()) {
            total++;
            if (edgeRiskScores.containsKey((long) baseEdges.getEdge())) matched++;
        }
        double rate = (total > 0) ? (double) matched / total : 0.0;
        System.out.printf("[CrimeWeighting] Startup validation: %d/%d edges "
            + "matched in JSON (%.1f%%)%n", matched, total, rate * 100);
        if (rate < minMatchRate) {
            System.err.printf("[CrimeWeighting] WARNING: match rate %.1f%% < "
                + "%.0f%% threshold. Re-run: python -m ml.build_edge_risk%n",
                rate * 100, minMatchRate * 100);
        }
    }
}
