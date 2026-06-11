#!/bin/sh
set -e

# ── Pre-flight: PBF must exist ──────────────────────────────────────────────
if [ ! -f /data/delhi-ncr-latest.osm.pbf ]; then
    echo "ERROR: /data/delhi-ncr-latest.osm.pbf not found."
    echo "Download: https://download.geofabrik.de/asia/india/delhi-latest.osm.pbf"
    echo "Save to:  graphhopper/data/delhi-ncr-latest.osm.pbf"
    exit 1
fi

# ── Stale-data guard: compare edge counts ───────────────────────────────────
# If edge_risk.json records a different gh_edges_csv_rows than the CSV has,
# the JSON was built from a different graph and crime routing will be wrong.
if [ -f /data/edge_risk.json ] && [ -f /data/gh_edges.csv ]; then
    JSON_ROWS=$(python3 -c "import json,sys; d=json.load(open('/data/edge_risk.json')); print(d.get('metadata',{}).get('gh_edges_csv_rows',0))" 2>/dev/null || echo "0")
    CSV_ROWS=$(( $(wc -l < /data/gh_edges.csv) - 1 ))  # subtract header row
    if [ "$JSON_ROWS" != "0" ] && [ "$JSON_ROWS" != "$CSV_ROWS" ]; then
        echo "WARNING: edge_risk.json was built from $JSON_ROWS edges; gh_edges.csv has $CSV_ROWS."
        echo "Re-run: python -m ml.build_edge_risk && docker compose restart graphhopper"
        echo "Starting with bootstrap config (fastest only) until JSON is regenerated."
        exec java $JAVA_OPTS -jar /app/graphhopper.jar server /app/config-bootstrap.yml
    fi
fi

# ── Choose config based on whether edge_risk.json exists ────────────────────
if [ ! -f /data/edge_risk.json ]; then
    echo "WARNING: /data/edge_risk.json not found."
    echo "Crime-aware profiles unavailable. Starting with fastest-only config."
    echo "After Stage 0 (EdgeExporter), run: python -m ml.build_edge_risk"
    echo "Then: docker compose restart graphhopper"
    exec java $JAVA_OPTS -jar /app/graphhopper.jar server /app/config-bootstrap.yml
fi

echo "edge_risk.json found. Starting with full config (fastest + balanced + safest)."
exec java $JAVA_OPTS -jar /app/graphhopper.jar server /app/config.yml
