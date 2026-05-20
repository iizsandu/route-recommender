from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_MODEL_NAME = "BAAI/bge-small-en-v1.5"
# WHY: 64 is safe on CPU for bge-small (~130MB model); larger batches don't help on CPU
_BATCH_SIZE = 64

_model: Optional[SentenceTransformer] = None


def _load_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading bge-small embedding model (one-time download ~130 MB)...")
        _model = SentenceTransformer(_MODEL_NAME)
        logger.info("bge-small ready")
    return _model


def encode(texts: list[str]) -> np.ndarray:
    """
    Encode texts to L2-normalised dense vectors.

    Args:
        texts: list of strings to encode

    Returns:
        np.ndarray of shape (len(texts), 384), dtype float32, L2-normalised
    """
    if not texts:
        return np.empty((0, 384), dtype=np.float32)

    model = _load_model()

    # WHY: normalize_embeddings=True produces unit vectors so cosine similarity
    # equals dot product — Qdrant's cosine index is faster with pre-normalised vectors
    vectors = model.encode(
        texts,
        batch_size=_BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 200,  # WHY: only show bar for long runs
    )

    return vectors.astype(np.float32)
