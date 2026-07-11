"""
FastAPI application entry point.

Responsibilities (grow with each phase):
  P0-1: create app, expose /health
  P0-5: add CORS middleware, request-ID middleware, structured logging
  P1-4: load KDE model in lifespan startup
  P0-2: initialise Cosmos client singleton in lifespan startup
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import Settings
from app.routers import agent as agent_router
from app.routers import geocode as geocode_router
from app.routers import risk as risk_router
from app.routers import routes as routes_router
from app.routers import search as search_router
from app.services import agent_service, retrieval_service, whisper_service
from app.services.risk_model import (
    load_lightgbm_models,
    load_model,
    reload_from_registry,
)
from app.utils.limiter import limiter
from app.utils.logger import configure as configure_logging
from app.utils.logger import get_logger, request_id_var

settings = Settings()


async def _check_graphhopper_health(
    url: str, retries: int = 3, delay: float = 10.0
) -> bool:
    """
    Ping GH's /health endpoint up to `retries` times.
    Returns True on the first 200 response, False if all attempts fail.
    WHY retry loop: GH may still be starting up when the backend lifespan runs
    (especially on cold starts without docker-compose healthcheck enforcement).
    """
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url}/health")
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        if attempt < retries - 1:
            await asyncio.sleep(delay)
    return False


async def _hot_reload_loop(interval_seconds: int) -> None:
    """Background task: check MLflow registry every interval_seconds and
    swap in a newer Production model if one exists.

    WHY check every hour rather than exactly at 21:30 UTC: simpler code,
    and the hourly poll costs nothing (it's just an MLflow metadata query
    until a new version appears). The retrain workflow finishes well within
    an hour of its 20:30 UTC start, so the new model is picked up promptly.
    """
    logger = get_logger("hot_reload")
    while True:
        await asyncio.sleep(interval_seconds)
        # WHY run_in_executor: reload_from_registry does blocking I/O
        # (pickle.load, MLflow HTTP/SQLite calls). Running it on the default
        # thread-pool executor prevents it from blocking the asyncio event loop
        # and stalling in-flight route requests during the swap.
        loop = asyncio.get_event_loop()
        reloaded = await loop.run_in_executor(None, reload_from_registry)
        if reloaded:
            logger.info("model hot-reload completed by background task")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(settings.LOG_FORMAT, settings.LOG_LEVEL)
    logger = get_logger("startup")
    logger.info("backend starting", log_format=settings.LOG_FORMAT)

    # Load KDE model once at startup — shared across all requests.
    # WHY resolve relative to repo root: KDE_ARTIFACTS_DIR may be a relative
    # path like "ml/artifacts". When uvicorn runs from backend/, that resolves
    # to backend/ml/artifacts which doesn't exist. We anchor it to the repo root
    # (two levels up from backend/app/) so it works from any working directory.
    _repo_root = Path(__file__).resolve().parents[2]  # backend/app/main.py → repo root
    artifacts_path = Path(settings.KDE_ARTIFACTS_DIR)
    if not artifacts_path.is_absolute():
        artifacts_path = _repo_root / artifacts_path
    try:
        load_model(artifacts_path)
    except Exception as exc:
        # WHY non-fatal: CI-built images have no pkl files (ml/artifacts/ is gitignored).
        # The container still starts and serves /health. Routing requests return 503
        # until a real image (built locally with artifacts) is deployed.
        logger.warning("KDE model load failed — routing unavailable", error=str(exc))

    # Load LightGBM models if the feature flag is on.
    # WHY optional: LGB artifacts may not exist on first deploy. Setting
    # USE_LIGHTGBM=False (the default) skips this block entirely so the
    # backend stays up even if train_lightgbm.py has never been run.
    if settings.USE_LIGHTGBM:
        lgb_path = Path(settings.LGB_ARTIFACTS_DIR)
        if not lgb_path.is_absolute():
            lgb_path = _repo_root / lgb_path
        try:
            load_lightgbm_models(lgb_path)
            logger.info("lightgbm ensemble enabled")
        except Exception as exc:
            logger.warning("LightGBM model load failed — ensemble disabled", error=str(exc))

    # Initialise retrieval service (Qdrant + bge-small + BM25).
    # WHY non-fatal: retrieval is local-only; production runs without Qdrant.
    # init() returns False gracefully if QDRANT_HOST is empty or unreachable.
    bm25_path = Path(settings.BM25_MODEL_PATH)
    if not bm25_path.is_absolute():
        bm25_path = _repo_root / bm25_path
    retrieval_service.init(
        qdrant_host=settings.QDRANT_HOST,
        qdrant_port=settings.QDRANT_PORT,
        qdrant_url=settings.QDRANT_URL,
        qdrant_api_key=settings.QDRANT_API_KEY,
        bm25_model_path=bm25_path,
    )

    # Initialise voice AI agent (Whisper + CrewAI).
    # WHY non-fatal: if Ollama is unreachable or model not pulled,
    # _ready stays False and /agent/query returns 503 — rest of app unaffected.
    whisper_service.init(
        model_size=settings.WHISPER_MODEL_SIZE,
        device=settings.WHISPER_DEVICE,
    )
    agent_service.init(
        provider=settings.LLM_PROVIDER,
        ollama_url=settings.OLLAMA_BASE_URL,
        ollama_model=settings.OLLAMA_MODEL,
        groq_key=settings.GROQ_API_KEY,
        groq_model=settings.GROQ_MODEL,
        anthropic_key=settings.ANTHROPIC_API_KEY,
        agent_model=settings.AGENT_MODEL,
    )

    # ── GraphHopper health check ──────────────────────────────────────────────
    # Tries up to 3 times (10s apart) so transient startup delays don't trigger
    # a warning. Logs WARNING (not error) — the backend still starts; routing
    # requests return 503 until GH is ready. In docker compose this is a
    # belt-and-suspenders check; depends_on: service_healthy already guarantees
    # GH is ready before the backend container starts.
    gh_healthy = await _check_graphhopper_health(settings.GRAPHHOPPER_URL)
    if not gh_healthy:
        logger.warning(
            "GraphHopper not reachable at startup",
            url=settings.GRAPHHOPPER_URL,
            note="routing requests will return 503 until GH is available",
        )
    else:
        logger.info("GraphHopper health check passed", url=settings.GRAPHHOPPER_URL)

    # Start background reload loop — checks MLflow registry every hour.
    reload_task = asyncio.create_task(
        _hot_reload_loop(settings.MODEL_RELOAD_INTERVAL_SECONDS)
    )

    yield

    # WHY cancel not await: the loop sleeps for up to an hour. cancel()
    # raises CancelledError inside the sleep, which unblocks shutdown immediately.
    reload_task.cancel()
    logger.info("backend shutting down")


# add this class above create_app
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # WHY: set before call_next so the ID is available in all downstream
        # log calls for this request, including inside service functions
        token = request_id_var.set(str(uuid.uuid4()))
        t0 = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        response.headers["X-Request-ID"] = request_id_var.get()
        # WHY log here not in each router: one place captures every endpoint,
        # including /health, without instrumenting each handler individually.
        _mw_logger = get_logger("request")
        _mw_logger.info(
            "request completed",
            path=request.url.path,
            method=request.method,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        request_id_var.reset(token)
        return response


def create_app() -> FastAPI:
    """
    Application factory.

    WHY factory, not module-level instantiation: importing this module in
    tests does not trigger side effects. Tests call create_app() explicitly
    and can pass different settings or mock dependencies.
    """

    app = FastAPI(
        title="Route Recommender API",
        description="Crime-aware route recommendations for Delhi-NCR female commuters.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS.split(","),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SlowAPIMiddleware)
    app.state.limiter = limiter
    # WHY _rate_limit_exceeded_handler: slowapi's built-in handler returns 429
    # with a Retry-After header set to the window reset time automatically.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(routes_router.router)
    app.include_router(risk_router.router)
    app.include_router(search_router.router)
    app.include_router(geocode_router.router)
    app.include_router(agent_router.router)



    Instrumentator().instrument(app).expose(app)

    return app

app = create_app()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

