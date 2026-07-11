"""
POST /agent/query — receive audio, transcribe, run CrewAI agent, return answer.
POST /agent/chat  — receive plain text, run CrewAI agent, return answer.
"""
import asyncio

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.schemas.routes import AgentResponse
from app.services import agent_service, whisper_service
from app.utils.limiter import limiter
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


class ChatRequest(BaseModel):
    message: str


@router.post("/chat", response_model=AgentResponse)
@limiter.limit("20/minute")
async def chat(request: Request, body: ChatRequest) -> AgentResponse:
    """Accept plain text, skip transcription, run CrewAI agent, return answer."""
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    response_text = await asyncio.to_thread(agent_service.query, message)
    return AgentResponse(transcript=message, response=response_text)


@router.post("/query", response_model=AgentResponse)
@limiter.limit("10/minute")
async def query(request: Request, audio: UploadFile = File(...)) -> AgentResponse:
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty.")

    # WHY to_thread: Whisper and CrewAI are both CPU-bound blocking calls.
    # Running them directly in the async endpoint would stall the entire
    # FastAPI event loop, blocking all other requests for 5-30 seconds.
    transcript = await asyncio.to_thread(whisper_service.transcribe, audio_bytes)
    if not transcript:
        raise HTTPException(
            status_code=503,
            detail="Transcription unavailable or audio contained no speech.",
        )

    logger.info("Transcript received", transcript=transcript[:80])

    response_text = await asyncio.to_thread(agent_service.query, transcript)

    return AgentResponse(transcript=transcript, response=response_text)

