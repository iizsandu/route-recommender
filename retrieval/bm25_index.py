from __future__ import annotations

import pickle
import re
from pathlib import Path

from rank_bm25 import BM25Okapi
from qdrant_client.models import SparseVector


def _tokenize(text: str) -> list[str]:
    # WHY: lowercase + split on non-alphanumeric — consistent tokenisation between
    # index-build time and query time so vocabulary indices stay aligned
    return re.findall(r"[a-z0-9]+", text.lower())


def fit(corpus: list[str]) -> BM25Okapi:
    """
    Fit BM25 on the full corpus of document texts.

    The fitted object holds vocabulary statistics (IDF scores) needed
    to compute sparse vectors at both index and query time.
    """
    tokenized = [_tokenize(doc) for doc in corpus]
    return BM25Okapi(tokenized)


def corpus_sparse_vector(
    bm25: BM25Okapi, doc_tokens: list[str], top_n: int = 200
) -> SparseVector:
    """
    Compute a sparse vector for a single document at index build time.

    Keeps only the top_n highest-scoring terms to keep Qdrant payload small.
    """
    # WHY: get_scores() returns a score for every term in the BM25 vocabulary
    # against the given document tokens (not against a query — BM25 can do this)
    scores = bm25.get_scores(doc_tokens)

    # WHY: argsort descending, keep top_n — most article texts use < 200 unique terms
    top_indices = scores.argsort()[::-1][:top_n]
    top_indices = top_indices[scores[top_indices] > 0]  # drop zero-score terms

    return SparseVector(
        indices=top_indices.tolist(),
        values=scores[top_indices].tolist(),
    )


def query_sparse_vector(bm25: BM25Okapi, query: str) -> SparseVector:
    """
    Compute a sparse vector for a search query at serving time.

    Uses the same fitted BM25 vocabulary so indices align with the index.
    """
    tokens = _tokenize(query)
    scores = bm25.get_scores(tokens)

    nonzero = scores.nonzero()[0]
    return SparseVector(
        indices=nonzero.tolist(),
        values=scores[nonzero].tolist(),
    )


def save(bm25: BM25Okapi, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(bm25, f)


def load(path: Path) -> BM25Okapi:
    with open(path, "rb") as f:
        return pickle.load(f)
