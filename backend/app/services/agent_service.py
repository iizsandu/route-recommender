"""
CrewAI agent service — single safety-expert agent with 3 tools.
LLM is selected at init time via LLM_PROVIDER env var (ollama or anthropic).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import numpy as np
from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import tool

from app.config import Settings
from app.services import retrieval_service, risk_model
from app.services.geocoding import geocode
from app.services.routing import get_routes
from app.utils.logger import get_logger

logger = get_logger(__name__)

_agent: Agent | None = None
_ready: bool = False
_settings = Settings()


def _band(score: float) -> str:
    """Convert raw KDE score to Low/Medium/High using calibrated thresholds."""
    if score < _settings.BAND_LOW_THRESHOLD:
        return "Low"
    if score < _settings.BAND_HIGH_THRESHOLD:
        return "Medium"
    return "High"


@tool("get_area_safety")
def get_area_safety(location: str) -> str:
    """
    Get the crime risk level and recent incident summary for a location in Delhi-NCR.
    Use when the user asks about safety of a specific area, neighbourhood, or landmark.
    Input: a place name or address string such as 'Connaught Place' or 'Lajpat Nagar'.
    """
    coords = asyncio.run(geocode(location))
    if coords is None:
        return f"Could not find '{location}' on the map."
    lat, lng = coords

    hour = datetime.now(timezone.utc).astimezone().hour
    scores = risk_model.score_points_batch(
        np.array([lat]), np.array([lng]), hour=hour
    )
    band = _band(float(scores[0]))

    incidents = retrieval_service.get_nearby_incidents(lat, lng, radius_km=1.5, top_k=5)
    crime_types = list({i.get("crime_macro", "") for i in incidents if i.get("crime_macro")})

    return (
        f"Risk level at {location}: {band}\n"
        f"Nearby incidents (last 90 days): {len(incidents)}\n"
        f"Crime types: {', '.join(crime_types) if crime_types else 'none on record'}"
    )


@tool("get_route_safety")
def get_route_safety(origin: str, destination: str, departure_time: str = "now") -> str:
    """
    Compare route safety when travelling between two places in Delhi-NCR.
    Use when the user asks about travelling from one location to another.
    Inputs: origin and destination as place names or address strings.
    """
    origin_coords = asyncio.run(geocode(origin))
    dest_coords = asyncio.run(geocode(destination))
    if origin_coords is None:
        return f"Could not find origin: '{origin}'"
    if dest_coords is None:
        return f"Could not find destination: '{destination}'"

    routes = asyncio.run(get_routes(origin_coords, dest_coords))
    if not routes:
        return "No routes found between these locations."

    now = datetime.now(timezone.utc)
    scored = []
    for r in routes:
        result = risk_model.score_route(
            r["waypoints"], depart_time=now, route_eta_sec=r.get("duration_sec", 0)
        )
        scored.append((_band(result.total_score), r.get("profile", "route")))

    band_order = {"Low": 0, "Medium": 1, "High": 2}
    scored.sort(key=lambda x: band_order[x[0]])
    lines = [f"- {profile}: {band} risk" for band, profile in scored]

    return (
        f"Routes from {origin} to {destination}:\n"
        + "\n".join(lines)
        + f"\nSafest: {scored[0][1]} ({scored[0][0]} risk)"
    )


@tool("search_crime_incidents")
def search_crime_incidents(query: str, location: str, radius_km: float = 2.0) -> str:
    """
    Search for specific types of crime incidents near a location in Delhi-NCR.
    Use when the user asks about a specific crime type (robbery, assault, etc.) in an area.
    Inputs: a natural-language crime description and a place name or address.
    """
    coords = asyncio.run(geocode(location))
    if coords is None:
        return f"Could not find '{location}' on the map."
    lat, lng = coords

    incidents = retrieval_service.get_nearby_incidents(
        lat, lng, radius_km=radius_km, top_k=8
    )
    if not incidents:
        return f"No incidents found within {radius_km} km of {location}."

    lines = []
    for i in incidents[:5]:
        crime = i.get("crime_macro", "Unknown")
        summary = (i.get("summary") or "No details")[:100]
        lines.append(f"- {crime}: {summary}")

    return f"Incidents near {location}:\n" + "\n".join(lines)


def init(
    provider: str,
    ollama_url: str,
    ollama_model: str,
    anthropic_key: str,
    agent_model: str,
) -> bool:
    global _agent, _ready
    try:
        if provider == "ollama":
            llm = LLM(model=f"ollama/{ollama_model}", base_url=ollama_url)
            logger.info("Agent LLM: Ollama", model=ollama_model, url=ollama_url)
        else:
            if not anthropic_key:
                logger.warning("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY empty — agent disabled")
                return False
            llm = LLM(model=f"anthropic/{agent_model}", api_key=anthropic_key)
            logger.info("Agent LLM: Anthropic", model=agent_model)

        _agent = Agent(
            role="Delhi Safety Expert",
            goal="Help female commuters in Delhi-NCR understand crime risk and make safe travel decisions.",
            backstory=(
                "You are a safety analyst with access to real-time crime data from the last 90 days "
                "across Delhi-NCR. You give concise, empathetic, actionable safety advice. "
                "Always use Low/Medium/High risk bands — never raw numbers. "
                "Focus on physical safety crimes: sexual violence, kidnapping, robbery, assault."
            ),
            tools=[get_area_safety, get_route_safety, search_crime_incidents],
            llm=llm,
            verbose=False,
            max_iter=5,
        )
        _ready = True
        logger.info("Agent service ready", provider=provider)
        return True
    except Exception as exc:
        logger.warning("Agent service init failed — voice queries disabled", error=str(exc))
        return False


def query(transcript: str) -> str:
    """Run the agent on the user's transcript. Returns plain text answer."""
    if not _ready or _agent is None:
        return "Safety assistant is not available right now."
    try:
        task = Task(
            description=(
                f"Answer this safety question from a female commuter in Delhi-NCR: {transcript}"
            ),
            expected_output="A concise 2-4 sentence safety assessment using Low/Medium/High risk bands.",
            agent=_agent,
        )
        crew = Crew(
            agents=[_agent],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )
        result = crew.kickoff()
        # WHY result.raw: crew.kickoff() returns CrewOutput, not a plain string.
        # .raw holds the final text answer from the last task.
        return str(result.raw).strip()
    except Exception as exc:
        logger.error("Agent query failed", error=str(exc), exc_info=True)
        return "Sorry, I could not process your question. Please try again."
