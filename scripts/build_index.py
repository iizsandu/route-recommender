"""
Build (or rebuild) the Qdrant search index from local MongoDB crime2 data.

Usage:
    python scripts/build_index.py                    # incremental (skip if collection exists)
    python scripts/build_index.py --rebuild          # drop + recreate collection
    python scripts/build_index.py --limit 500        # test run: first 500 records only
    python scripts/build_index.py --limit 500 --dry-run  # steps 1-5 only, no Qdrant write
"""
import argparse
import sys
from pathlib import Path

# WHY: allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrieval.pipeline import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Qdrant index from MongoDB crime2 data")
    parser.add_argument("--rebuild", action="store_true", help="Drop and recreate the collection")
    parser.add_argument("--dry-run", action="store_true", help="Skip Qdrant upsert (steps 1-5 only)")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N records")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017/")
    parser.add_argument("--qdrant-host", default="localhost")
    parser.add_argument("--qdrant-port", type=int, default=6333)
    args = parser.parse_args()

    run(
        mongo_uri=args.mongo_uri,
        qdrant_host=args.qdrant_host,
        qdrant_port=args.qdrant_port,
        rebuild=args.rebuild,
        dry_run=args.dry_run,
        limit=args.limit,
    )
