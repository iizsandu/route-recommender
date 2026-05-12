"""
FastAPI application entry point.

Responsibilities (grow with each phase):
  P0-1: create app, expose /health
  P0-5: add CORS middleware, request-ID middleware, structured logging
  P1-4: load KDE model in lifespan startup
  P0-2: initialise Cosmos client singleton in lifespan startup
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uuid
from app.utils.logger import configure as configure_logging, get_logger, request_id_var
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from app.config import Settings

settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs startup logic before yield; shutdown logic after yield.
    FastAPI calls this once per process, not once per request.

    P0-2: initialise Cosmos async client, store on app.state
    P1-4: load Production-stage KDE model from MLflow, store on app.state
    P0-5: close Cosmos client and flush structlog buffer on shutdown
    """
    configure_logging(settings.LOG_FORMAT, settings.LOG_LEVEL)
    logger = get_logger("startup")
    logger.info("backend starting", log_format=settings.LOG_FORMAT)
    yield
    logger.info("backend shutting down")


# add this class above create_app
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # WHY: set before call_next so the ID is available in all downstream
        # log calls for this request, including inside service functions
        token = request_id_var.set(str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id_var.get()
        # WHY: reset after response so the ContextVar slot is clean for the
        # next request that reuses this async task
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
    # Phase 1: include routers/routes.py and routers/risk.py

    return app

app = create_app()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

