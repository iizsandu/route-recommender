# backend/app/schemas/routes.py

from datetime import datetime, timezone
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


class LatLng(BaseModel):
    lat: float
    lng: float


class RouteRequest(BaseModel):
    # WHY Union not X|Y: Python 3.9 doesn't support the X|Y union syntax in
    # runtime type annotations. Union[X, Y] works on 3.9+.
    # (from __future__ import annotations defers evaluation but Pydantic 2
    # still evaluates them at model construction time via get_type_hints.)
    origin:      Union[LatLng, str]
    destination: Union[LatLng, str]
    depart_time: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class IncidentResult(BaseModel):
    """A historically reported crime incident near a route segment."""
    crime_macro:    Optional[str] = None
    crime_type:     Optional[str] = None
    lat:            Optional[float] = None
    lng:            Optional[float] = None
    crime_date:     Optional[str] = None
    summary:        str
    url:            str
    location_exact: Optional[str] = None
    victim:         Optional[str] = None
    weapon_used:    Optional[str] = None
    # WHY rrf_score exposed: useful for frontend to sort/filter if needed,
    # and helps during debugging to see why an incident ranked where it did.
    rrf_score:      float = 0.0


class PersonalisedRequest(BaseModel):
    situation: str
    waypoints: list[tuple[float, float]]
    radius_km: float = 2.0
    max_total: int = 8


class RouteOption(BaseModel):
    geometry:     dict        # GeoJSON LineString
    duration_sec: float
    distance_m:   float
    # WHY Literal: enforces the 3-band contract at the type level. The raw
    # float score is never returned to the client — only the band label.
    risk_band:    Literal["Low", "Medium", "High"]
    # Which GH profile produced this route — "fastest", "balanced", or "safest".
    # Used by the frontend to assign colour and label independently of risk_band.
    route_type:   str = "balanced"
    # WHY default=[]: if Qdrant is unavailable, the field is present but empty
    # rather than absent — frontend doesn't need to null-check the field.
    nearby_incidents: list[IncidentResult] = []


class RouteResponse(BaseModel):
    routes: list[RouteOption]
    framing_note: str = (
        "Nearby incidents represent historically reported crimes from news sources. "
        "This is not a prediction of future crime."
    )

class AgentResponse(BaseModel):
    transcript: str   # what Whisper heard the user say
    response: str     # what the CrewAI agent answered
