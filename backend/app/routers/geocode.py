from fastapi import APIRouter, HTTPException, Query
from app.services.geocoding import geocode

router = APIRouter()


@router.get("/geocode")
async def geocode_address(q: str = Query(..., description="Address to geocode")):
    try:
        lat, lng = await geocode(q)
        return {"lat": lat, "lng": lng, "query": q}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
