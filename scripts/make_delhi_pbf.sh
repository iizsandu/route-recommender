#!/usr/bin/env bash
#
# make_delhi_pbf.sh — Build a minimal Delhi-NCR .osm.pbf from Geofabrik.
#
# Why two downloads: Delhi-NCR straddles TWO Geofabrik India zones, and
# Geofabrik has NO standalone Delhi extract:
#   - Northern Zone -> Delhi (NCT), Haryana (Gurgaon, Faridabad), Rajasthan, ...
#   - Central Zone  -> Uttar Pradesh (Noida, Ghaziabad, Greater Noida), ...
# So we download both zones, clip each to the project bbox, then merge.
#
# Requires osmium-tool:
#   Ubuntu/Debian : sudo apt-get install osmium-tool
#   macOS (brew)  : brew install osmium-tool
#   Windows       : conda install -c conda-forge osmium-tool  (or run under WSL)
#
# Usage : ./make_delhi_pbf.sh [workdir]
# Output: <workdir>/delhi_ncr.osm.pbf

set -euo pipefail

# --- Config -----------------------------------------------------------------
# Bounding box = the project's Delhi-NCR bounds (CLAUDE.md): lat 28.0–29.5, lng 76.5–78.0
# IMPORTANT: osmium bbox order is WEST,SOUTH,EAST,NORTH (minlon,minlat,maxlon,maxlat)
BBOX="76.5,28.0,78.0,29.5"

WORKDIR="${1:-./delhi_build}"
NORTH_URL="https://download.geofabrik.de/asia/india/northern-zone-latest.osm.pbf"
CENTRAL_URL="https://download.geofabrik.de/asia/india/central-zone-latest.osm.pbf"

mkdir -p "$WORKDIR"
cd "$WORKDIR"

# --- 1. Download both zones (resumable via -C -) ----------------------------
echo ">> Downloading Geofabrik zones (~209 MB + ~331 MB)..."
curl -L -C - -o northern-zone-latest.osm.pbf "$NORTH_URL"
curl -L -C - -o central-zone-latest.osm.pbf  "$CENTRAL_URL"

# --- 2. Clip each zone to the Delhi-NCR bbox --------------------------------
# Default strategy 'complete_ways' keeps ways whole across the border, so roads
# are not cut mid-segment — important for routing engines (OSRM/Valhalla).
echo ">> Clipping Northern Zone..."
osmium extract -b "$BBOX" northern-zone-latest.osm.pbf -o delhi_north.osm.pbf --overwrite

echo ">> Clipping Central Zone..."
osmium extract -b "$BBOX" central-zone-latest.osm.pbf  -o delhi_central.osm.pbf --overwrite

# --- 3. Merge the two clips into one minimal file ---------------------------
# Safe: osmium extract never drops referenced nodes, and merge de-duplicates
# objects that appear in both clips at the zone seam.
echo ">> Merging..."
osmium merge delhi_north.osm.pbf delhi_central.osm.pbf -o delhi_ncr.osm.pbf --overwrite

# --- 4. Report --------------------------------------------------------------
echo ">> Done: $WORKDIR/delhi_ncr.osm.pbf"
osmium fileinfo delhi_ncr.osm.pbf | grep -E "Bounding box|Size:" || true
ls -lh delhi_ncr.osm.pbf

# Optional cleanup of intermediates (uncomment to save disk):
# rm -f northern-zone-latest.osm.pbf central-zone-latest.osm.pbf delhi_north.osm.pbf delhi_central.osm.pbf
