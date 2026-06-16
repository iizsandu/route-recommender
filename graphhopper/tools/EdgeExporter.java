/*
 * Exports every base edge from a compiled GraphHopper graph to a CSV file.
 * Run after GraphHopper has built the graph cache (first startup).
 * The output CSV is used by ml/build_edge_risk.py to compute per-edge crime scores.
 *
 * Usage:
 *   java -cp "/app/graphhopper-tools.jar:/app/graphhopper.jar" \
 *        com.graphhopper.tools.EdgeExporter \
 *        <graph-cache-dir> <output-csv-path> <config.yml>
 *
 * Example:
 *   java -cp "/app/graphhopper-tools.jar:/app/graphhopper.jar" \
 *        com.graphhopper.tools.EdgeExporter \
 *        /graphhopper/graph-cache /data/gh_edges.csv /app/config-bootstrap.yml
 *
 * WHY the config argument: GH 9.x stores a profile fingerprint in the graph cache
 * and validates it against the running config on every load(). Without a matching
 * profile config, load() throws IllegalStateException. Passing the same config.yml
 * that was used to build the graph makes the fingerprints match.
 */
package com.graphhopper.tools;

import com.graphhopper.GraphHopper;
import com.graphhopper.application.GraphHopperServerConfiguration;
import com.graphhopper.jackson.GraphHopperModule;
import com.graphhopper.routing.util.AllEdgesIterator;
import com.graphhopper.util.FetchMode;
import com.graphhopper.util.PointList;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.Locale;

public class EdgeExporter {

    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("Usage: EdgeExporter <graph-cache-dir> <output-csv-path> <config.yml>");
            System.exit(1);
        }
        String graphDir   = args[0];
        String csvPath    = args[1];
        String configPath = args[2];

        System.out.println("[EdgeExporter] Loading graph from: " + graphDir);
        System.out.println("[EdgeExporter] Using config:       " + configPath);

        // WHY parse config file: GH 9.x validates profile fingerprints stored in the
        // graph cache against the profile list in the running config. An empty profile
        // list (the default when no config is provided) always mismatches. Loading the
        // same config.yml used to build the graph produces matching fingerprints.
        ObjectMapper mapper = new ObjectMapper(new YAMLFactory())
                .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false)
                .registerModule(new GraphHopperModule());
        GraphHopperServerConfiguration serverCfg = mapper.readValue(
                new File(configPath), GraphHopperServerConfiguration.class);

        GraphHopper gh = new GraphHopper().init(serverCfg.getGraphHopperConfiguration());
        // init() sets graphHopperLocation from config; override with the explicit arg
        // so this tool works even if the config points to a different path.
        gh.setGraphHopperLocation(graphDir);

        boolean loaded = gh.load();
        if (!loaded) {
            System.err.println("[EdgeExporter] ERROR: Could not load graph from " + graphDir);
            System.err.println("Make sure GraphHopper has completed its first import.");
            System.exit(1);
        }

        System.out.println("[EdgeExporter] Graph loaded. Iterating edges...");

        long edgeCount = 0;
        long skipped   = 0;

        try (PrintWriter out = new PrintWriter(new BufferedWriter(new FileWriter(csvPath)))) {
            out.println("edge_id,length_m,geom_wkt");

            // AllEdgesIterator is cursor-style (like a database ResultSet): call .next()
            // to advance before reading. It does NOT implement Iterable, so for-each loops
            // do not compile against it.
            AllEdgesIterator edges = gh.getBaseGraph().getAllEdges();
            while (edges.next()) {
                PointList pts = edges.fetchWayGeometry(FetchMode.ALL);
                if (pts.isEmpty()) {
                    skipped++;
                    continue;
                }

                // Build WKT LINESTRING with coordinates in (longitude latitude) order.
                // WKT standard uses (x, y) = (lng, lat). Python's shapely_wkt.loads()
                // and geopandas parse this correctly in EPSG:4326.
                // Locale.ROOT is mandatory: JVM default locale can use decimal commas
                // (e.g. German locale), which would corrupt the WKT and break Python parsing.
                StringBuilder wkt = new StringBuilder("LINESTRING(");
                for (int i = 0; i < pts.size(); i++) {
                    if (i > 0) wkt.append(",");
                    wkt.append(String.format(Locale.ROOT, "%.6f %.6f",
                        pts.getLon(i), pts.getLat(i)));
                }
                wkt.append(")");

                out.printf(Locale.ROOT, "%d,%.2f,%s%n",
                    edges.getEdge(),      // integer edge ID — key used in edge_risk.json
                    edges.getDistance(),  // metres
                    wkt);
                edgeCount++;
            }
        }

        System.out.printf("[EdgeExporter] Done. Wrote %d edges to %s (%d skipped).%n",
            edgeCount, csvPath, skipped);
    }
}
