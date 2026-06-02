from __future__ import annotations

import logging

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

logger = logging.getLogger(__name__)

COLLECTION_NAME = "delhi_crimes"
DENSE_SIZE = 384


def get_client(
    host: str = "localhost",
    port: int = 6333,
    url: str = "",
    api_key: str = "",
) -> QdrantClient:
    if url:
        return QdrantClient(url=url, api_key=api_key or None)
    return QdrantClient(host=host, port=port)


def create_collection(
    client: QdrantClient,
    collection_name: str = COLLECTION_NAME,
    recreate: bool = False,
) -> None:
    existing = [c.name for c in client.get_collections().collections]

    if collection_name in existing:
        if recreate:
            logger.info("Dropping existing collection %s for rebuild", collection_name)
            client.delete_collection(collection_name)
        else:
            logger.info("Collection %s already exists — skipping creation", collection_name)
            return

    # WHY: cosine distance on pre-normalised vectors is equivalent to dot product
    # but Qdrant still needs to know which metric to use for HNSW index construction.
    # WHY dicts not VectorsConfig(...): VectorsConfig/SparseVectorsConfig are Union
    # type aliases in qdrant-client — they cannot be instantiated directly.
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "dense": VectorParams(size=DENSE_SIZE, distance=Distance.COSINE)
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(
                index=SparseIndexParams(on_disk=False)
                # WHY on_disk=False: keeps the sparse index in RAM for faster keyword search
            )
        },
    )
    logger.info("Created collection %s", collection_name)


def upsert_batch(
    client: QdrantClient,
    points: list[PointStruct],
    collection_name: str = COLLECTION_NAME,
) -> None:
    # WHY: Qdrant upsert is idempotent on point id — safe to re-run if pipeline crashes
    client.upsert(collection_name=collection_name, points=points)
    logger.debug("Upserted %d points", len(points))
