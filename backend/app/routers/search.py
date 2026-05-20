# backend/app/routers/search.py
# Feature D — semantic search across all indexed Delhi crime incidents.
# Returns [] gracefully when Qdrant is not running.

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import retrieval_service
from app.schemas.routes import IncidentResult

router = APIRouter(prefix="/search", tags=["search"])

_FRAMING_NOTE = (
    "Results represent historically reported incidents from news sources. "
    "This is not a prediction of future crime."
)


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=50)


class SearchResponse(BaseModel):
    query: str
    results: list[IncidentResult]
    total_returned: int
    framing_note: str = _FRAMING_NOTE


@router.post("/incidents", response_model=SearchResponse)
async def search_incidents(req: SearchRequest) -> SearchResponse:
    """
    Feature D — text search across all indexed Delhi crime incidents.

    Requires Qdrant to be running locally (set QDRANT_HOST=localhost in .env).
    Returns an empty result list if retrieval is not available.
    """
    if not retrieval_service._ready:
        # WHY return empty rather than 503: lets the frontend render gracefully
        # without special-casing the error. The framing_note explains the state.
        return SearchResponse(
            query=req.query,
            results=[],
            total_returned=0,
            framing_note="Incident search is not available (Qdrant not configured).",
        )

    try:
        from retrieval.search import hybrid_search
        raw = hybrid_search(
            client=retrieval_service._client,
            embed_model=retrieval_service._embed_model,
            bm25=retrieval_service._bm25,
            query_text=req.query,
            lat=None,   # WHY None: Feature D has no geo filter — search all of Delhi
            lng=None,
            top_k=req.top_k,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search failed: {exc}") from exc

    results = [
        IncidentResult(
            crime_macro=i.get("crime_macro", "Unknown"),
            crime_type=i.get("crime_type"),
            lat=i.get("lat"),
            lng=i.get("lng"),
            crime_date=i.get("crime_date") or None,
            summary=i.get("summary", ""),
            url=i.get("url", ""),
            location_exact=i.get("location_exact"),
            rrf_score=i.get("rrf_score", 0.0),
        )
        for i in raw
    ]

    return SearchResponse(
        query=req.query,
        results=results,
        total_returned=len(results),
    )
