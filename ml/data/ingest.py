# ml/data/ingest.py
"""
Fetch raw crime records from Azure Cosmos DB, apply the full EDA-validated
cleaning pipeline, and write a dated Parquet snapshot.

Cleaning steps (matching crime_data_analysis_v2.py exactly):
  1.  Strip Cosmos metadata keys
  2.  Filter is_crime == True
  3.  Map crime_type → crime_macro via regex priority list
  4.  Parse coordinates dict {"lat": ..., "lng": ...} → lat, lng columns
  5.  Parse article_date and crime_date to datetime
  6.  Build effective_date (crime_date, fallback to article_date)
  7.  Derive year column
  8.  Clamp future crime_date → article_date (158 extraction errors in EDA)
  9.  Flag is_historical (year < 2007)
  10. Null Bucket A coordinates (Delhi location_broad, out-of-bounds coords)
  11. Flag is_delhi_crime (location_broad contains 'Delhi')

The snapshot retains ALL rows that pass step 2 — including rows with null
coordinates and non-Delhi records. Downstream consumers (train_kde.py) apply
their own filters. ingest.py's job is faithful cleaning, not pre-filtering.

Production usage (Cosmos DB):
    python -m ml.data.ingest

Local dev bootstrap (raw JSON → Parquet snapshot, no Cosmos needed):
    python -m ml.data.ingest --from-json path/to/raw_15_05.json
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Geographic bounds (Delhi-NCR) ─────────────────────────────────────────────
LAT_MIN, LAT_MAX = 28.0, 29.5
LNG_MIN, LNG_MAX = 76.5, 78.0

# ── Output path ───────────────────────────────────────────────────────────────
SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# ── Cosmos metadata keys to strip before building DataFrame ───────────────────
_COSMOS_META = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_from_cosmos(
    connection_string: str,
    database: str,
    container: str,
) -> list[dict]:
    """Pull every document from the Cosmos container.

    WHY imported inside function: azure-cosmos is a production dependency.
    Importing at module level would break local dev environments that don't
    have it installed. The --from-json path never calls this function.
    """
    from azure.cosmos import CosmosClient  # noqa: PLC0415

    client = CosmosClient.from_connection_string(connection_string)
    db     = client.get_database_client(database)
    ctr    = db.get_container_client(container)

    # WHY enable_cross_partition_query: container is partitioned by /id so
    # SELECT * is always a cross-partition fan-out. Without this flag the
    # SDK raises a 400 error.
    items = list(ctr.query_items(
        query="SELECT * FROM c",
        enable_cross_partition_query=True,
    ))
    logger.info("fetched %d raw records from Cosmos", len(items))
    return items


def _load_from_json(json_path: Path) -> list[dict]:
    """Load raw records from a local JSON file (dev bootstrap path)."""
    logger.info("loading records from local JSON: %s", json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        records = json.load(f)
    logger.info("loaded %d raw records from JSON", len(records))
    return records


def _parse_coordinates(coord: object) -> tuple[float | None, float | None]:
    """Extract (lat, lng) from a coordinate dict.

    Coordinates arrive as {"lat": ..., "lng": ...} nested dicts in the raw
    Cosmos documents — NOT as top-level lat/lng columns.
    Returns (None, None) for nulls, non-dicts, or missing keys.
    """
    if coord is None or (isinstance(coord, float) and np.isnan(coord)):
        return None, None
    if isinstance(coord, dict):
        return coord.get("lat"), coord.get("lng")
    # Unexpected type (e.g. string) — fail safe rather than raise.
    return None, None


def _is_delhi_location(loc: object) -> bool:
    """True if location_broad contains 'Delhi' (covers 'New Delhi' too)."""
    if loc is None or (isinstance(loc, float) and pd.isna(loc)):
        return False
    return "Delhi" in str(loc)


# ─────────────────────────────────────────────────────────────────────────────
# Cleaning pipeline
# ─────────────────────────────────────────────────────────────────────────────

def _clean(records: list[dict]) -> pd.DataFrame:
    """Apply the full EDA-validated cleaning pipeline to raw records.

    Accepts records from either Cosmos DB or a local JSON file — the format
    is identical. Returns a DataFrame with ALL is_crime=True rows retained.
    Rows with null coordinates or non-Delhi locations are kept — downstream
    consumers (train_kde.py) apply their own filters.
    """
    # ── Step 1: strip Cosmos metadata ────────────────────────────────────────
    # Safe to run on JSON records too — they simply won't have these keys.
    clean = [{k: v for k, v in r.items() if k not in _COSMOS_META}
             for r in records]
    df = pd.DataFrame(clean)
    logger.info("records after metadata strip: %d", len(df))

    # ── Step 2: filter to confirmed crimes ───────────────────────────────────
    # WHY astype(str).str.lower(): is_crime may be bool True, int 1, or string
    # "true" depending on which LLM extraction version wrote the record.
    df["is_crime"] = (
        df.get("is_crime", pd.Series(dtype=object))
          .astype(str).str.lower()
    )
    df = df[df["is_crime"] == "true"].copy()
    logger.info("after is_crime=True filter: %d", len(df))

    # ── Step 3: macro category mapping ───────────────────────────────────────
    # Lazy import: category_mapping is always available, but keeping imports
    # grouped at the call site makes the dependency explicit.
    from ml.data.category_mapping import map_crime_macro  # noqa: PLC0415

    df["crime_macro"] = (
        df.get("crime_type", pd.Series(dtype=object))
          .apply(map_crime_macro)
    )

    # ── Step 4: coordinate parsing ────────────────────────────────────────────
    # Coordinates are a nested dict {"lat": ..., "lng": ...}, not top-level
    # columns. Parse them out before any coordinate-based filtering.
    df[["lat", "lng"]] = df["coordinates"].apply(
        lambda x: pd.Series(_parse_coordinates(x))
    )
    logger.info(
        "coordinates parsed — valid: %d, null: %d",
        df["lat"].notna().sum(), df["lat"].isna().sum(),
    )

    # ── Step 5: date parsing ──────────────────────────────────────────────────
    df["article_date"] = pd.to_datetime(df["article_date"], errors="coerce")
    df["crime_date"]   = pd.to_datetime(df["crime_date"],   errors="coerce")

    # ── Step 6: effective_date ────────────────────────────────────────────────
    # Primary: crime_date. Fallback: article_date. Null if neither exists.
    df["effective_date"] = df["crime_date"].fillna(df["article_date"])

    # ── Step 7: year column ───────────────────────────────────────────────────
    df["year"] = df["effective_date"].dt.year

    # ── Step 8: clamp future crime_date → article_date ────────────────────────
    # WHY: 158 records have crime_date > article_date — LLM extraction errors
    # where the model hallucinated a future date. A future crime_date inflates
    # recency weight in KDE training. Clamping to article_date is conservative.
    future_mask = (
        df["crime_date"].notna()
        & df["article_date"].notna()
        & (df["crime_date"] > df["article_date"])
    )
    df.loc[future_mask, "crime_date"]     = df.loc[future_mask, "article_date"]
    df.loc[future_mask, "effective_date"] = df.loc[future_mask, "article_date"]
    logger.info("future crime_date clamped to article_date: %d records", future_mask.sum())

    # ── Step 9: historical flag ───────────────────────────────────────────────
    # Records before 2007 are flagged but NOT dropped — kept for audit trail.
    # KDE training excludes them via the is_historical filter.
    df["is_historical"] = df["year"] < 2007
    logger.info("is_historical=True: %d records", df["is_historical"].sum())

    # ── Step 10: null Bucket A coordinates ───────────────────────────────────
    # Bucket A = Delhi location_broad + coordinates outside Delhi-NCR bounds.
    # These are geocoding failures (wrong coords, correct location text).
    # We null the coords rather than drop the row — the record still has valid
    # category, date, and location text for non-spatial analysis.
    has_coords    = df["lat"].notna()
    out_of_bounds = (
        (df["lat"] < LAT_MIN) | (df["lat"] > LAT_MAX) |
        (df["lng"] < LNG_MIN) | (df["lng"] > LNG_MAX)
    )
    bucket_a_mask = (
        has_coords
        & out_of_bounds
        & df["location_broad"].apply(_is_delhi_location)
    )
    df.loc[bucket_a_mask, "lat"] = np.nan
    df.loc[bucket_a_mask, "lng"] = np.nan
    logger.info("Bucket A coordinates nulled: %d records", bucket_a_mask.sum())

    # ── Step 11: Delhi crime flag ─────────────────────────────────────────────
    # Based on location_broad text, NOT coordinates. A record can be
    # is_delhi_crime=True with null coordinates (Bucket A records).
    df["is_delhi_crime"] = df["location_broad"].apply(_is_delhi_location)
    logger.info(
        "is_delhi_crime — True: %d, False: %d",
        df["is_delhi_crime"].sum(), (~df["is_delhi_crime"]).sum(),
    )

    return df


def _write_snapshot(df: pd.DataFrame, snapshot_dir: Path) -> Path:
    """Write the cleaned DataFrame to a dated Parquet snapshot."""
    if df.empty:
        # WHY raise rather than return: an empty snapshot would silently
        # produce a KDE model trained on zero points. Loud failure here
        # prevents a corrupt model from reaching production.
        raise ValueError("ingest produced 0 records after filtering — aborting")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    out_path = snapshot_dir / f"crimes_{date.today().isoformat()}.parquet"

    # WHY index=False: the integer index is meaningless — omitting it keeps
    # the Parquet file clean and avoids a confusing unnamed column on read.
    df.to_parquet(out_path, index=False)
    logger.info("snapshot written: %s  (%d records)", out_path, len(df))
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────

def run(
    connection_string: str,
    database: str,
    container: str,
    snapshot_dir: Path = SNAPSHOT_DIR,
) -> Path:
    """Production path: fetch from Cosmos DB, clean, and snapshot.

    Returns the path to the written Parquet snapshot.
    """
    records = _fetch_from_cosmos(connection_string, database, container)
    df      = _clean(records)
    return _write_snapshot(df, snapshot_dir)


def run_from_json(
    json_path: Path,
    snapshot_dir: Path = SNAPSHOT_DIR,
) -> Path:
    """Local dev bootstrap: clean a raw JSON file and write a Parquet snapshot.

    Identical cleaning pipeline to run() — only the data source differs.
    Use this once to bootstrap your local snapshot, then train_kde.py reads
    the Parquet file exactly as it would in production.

    Returns the path to the written Parquet snapshot.
    """
    records = _load_from_json(json_path)
    df      = _clean(records)
    return _write_snapshot(df, snapshot_dir)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import os
    import sys

    # Auto-load .env from repo root so this script works regardless of CWD.
    # Uses partition("=") so connection strings with embedded = signs are preserved.
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    _DOTENV = _REPO_ROOT / ".env"
    if _DOTENV.exists():
        for _raw in _DOTENV.read_text(encoding="utf-8").splitlines():
            _line = _raw.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Ingest crime records and write a cleaned Parquet snapshot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Production — pull from Cosmos DB:
  python -m ml.data.ingest

  # Local dev — bootstrap from raw JSON:
  python -m ml.data.ingest --from-json path/to/raw_15_05.json
        """,
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        metavar="PATH",
        help="Path to a raw crimes JSON file. Skips Cosmos DB fetch entirely.",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=SNAPSHOT_DIR,
        help=f"Directory to write the Parquet snapshot (default: {SNAPSHOT_DIR})",
    )
    args = parser.parse_args()

    if args.from_json:
        # ── Local dev path ────────────────────────────────────────────────────
        if not args.from_json.exists():
            logger.error("JSON file not found: %s", args.from_json)
            sys.exit(1)
        out = run_from_json(args.from_json, snapshot_dir=args.snapshot_dir)
        print(f"\nSnapshot written: {out}")
        sys.exit(0)

    else:
        # ── Production path ───────────────────────────────────────────────────
        conn = os.environ.get("COSMOS_CONNECTION_STRING")
        if not conn:
            logger.error(
                "COSMOS_CONNECTION_STRING not set. "
                "Use --from-json for local dev without Cosmos."
            )
            sys.exit(1)
        out = run(
            connection_string=conn,
            database=os.environ.get("COSMOS_DATABASE_NAME", "route_recommender"),
            container=os.environ.get("COSMOS_CONTAINER_NAME", "structured_crimes"),
            snapshot_dir=args.snapshot_dir,
        )
        print(f"\nSnapshot written: {out}")
        sys.exit(0)
