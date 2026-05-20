from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pymongo import MongoClient
from tqdm import tqdm
from qdrant_client.models import PointStruct

from retrieval import summarise, embed, bm25_index, qdrant_store
from retrieval.bm25_index import corpus_sparse_vector

logger = logging.getLogger(__name__)

# Delhi-NCR bounds — same as the rest of the pipeline
_LAT_MIN, _LAT_MAX = 28.0, 29.5
_LNG_MIN, _LNG_MAX = 76.5, 78.0

BM25_MODEL_PATH = Path(__file__).parent / "bm25_model.pkl"
_UPSERT_BATCH_SIZE = 100


def _fetch_records(
    mongo_uri: str = "mongodb://localhost:27017/",
    db_name: str = "crime2",
    limit: Optional[int] = None,
) -> list[dict]:
    """
    Pull crime records from MongoDB, join with article text, filter to Delhi-NCR.
    Returns a list of dicts with all fields needed for indexing.
    """
    client = MongoClient(mongo_uri)
    db = client[db_name]

    # WHY coordinates.lat not lat: the MongoDB schema stores coordinates in a
    # nested dict {"coordinates": {"lat": ..., "lng": ...}}, not as top-level fields.
    query = {
        "is_crime": True,
        "coordinates.lat": {"$gte": _LAT_MIN, "$lte": _LAT_MAX},
        "coordinates.lng": {"$gte": _LNG_MIN, "$lte": _LNG_MAX},
    }
    cursor = db["extracted"].find(query)
    if limit:
        cursor = cursor.limit(limit)

    records = list(cursor)
    logger.info("Fetched %d crime records from MongoDB", len(records))

    # Build url → article text lookup in memory (faster than per-record $lookup)
    urls = {r["url"] for r in records if r.get("url")}
    articles = {
        doc["url"]: doc.get("text", "")
        for doc in db["articles2"].find({"url": {"$in": list(urls)}})
    }

    no_article = sum(1 for r in records if r.get("url") not in articles)
    if no_article:
        logger.warning("%d records have no matching article text", no_article)

    # Merge article text into each record
    for r in records:
        r["_article_text"] = articles.get(r.get("url", ""), "")

    client.close()
    return records


def run(
    mongo_uri: str = "mongodb://localhost:27017/",
    db_name: str = "crime2",
    qdrant_host: str = "localhost",
    qdrant_port: int = 6333,
    rebuild: bool = False,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Step 1 + 2: Fetch records and join article text
    records = _fetch_records(mongo_uri, db_name, limit)
    if not records:
        logger.error("No records fetched — aborting")
        return

    # Step 3: Summarize article texts
    texts = [r["_article_text"] for r in records]
    logger.info("Summarizing %d articles with distilbart...", len(texts))
    summaries = summarise.summarize_batch(texts)

    # Attach summaries back to records
    for r, summary in zip(records, summaries):
        r["summary"] = summary

    # Step 4: Embed summaries
    logger.info("Encoding %d summaries with bge-small...", len(summaries))
    dense_vectors = embed.encode(summaries)  # shape (N, 384)

    # Step 5: Fit BM25 on all summaries, compute per-doc sparse vectors
    logger.info("Fitting BM25 on corpus...")
    bm25 = bm25_index.fit(summaries)
    bm25_index.save(bm25, BM25_MODEL_PATH)
    logger.info("BM25 model saved to %s", BM25_MODEL_PATH)

    tokenized_summaries = [bm25_index._tokenize(s) for s in summaries]
    sparse_vectors = [
        corpus_sparse_vector(bm25, tokens)
        for tokens in tqdm(tokenized_summaries, desc="BM25 sparse vectors")
    ]

    if dry_run:
        logger.info("--dry-run: skipping Qdrant upsert. Pipeline steps 1-5 complete.")
        return

    # Step 6 + 7: Create collection and upsert
    qclient = qdrant_store.get_client(qdrant_host, qdrant_port)
    qdrant_store.create_collection(qclient, recreate=rebuild)

    points: list[PointStruct] = []
    for idx, (record, dense_vec, sparse_vec) in enumerate(
        zip(records, dense_vectors, sparse_vectors)
    ):
        coords = record.get("coordinates") or {}
        payload = {
            "crime_macro": record.get("crime_macro"),
            "crime_type": record.get("crime_type"),
            "lat": coords.get("lat"),
            "lng": coords.get("lng"),
            "crime_date": str(record.get("crime_date") or ""),
            "article_date": str(record.get("article_date") or ""),
            "url": record.get("url", ""),
            "summary": record["summary"],
            "location_exact": record.get("location_exact"),
            "severity_score": record.get("severity_score"),
        }
        points.append(
            PointStruct(
                id=idx,
                vector={"dense": dense_vec.tolist(), "sparse": sparse_vec},
                payload=payload,
            )
        )

        # WHY: flush every 100 points to avoid holding the entire dataset in RAM
        if len(points) == _UPSERT_BATCH_SIZE:
            qdrant_store.upsert_batch(qclient, points)
            points = []

    if points:
        qdrant_store.upsert_batch(qclient, points)

    final_count = qclient.count("delhi_crimes").count
    logger.info(
        "Index complete. Qdrant collection 'delhi_crimes' has %d points (input: %d records)",
        final_count,
        len(records),
    )
