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
    yield


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

    # P0-5: add CORSMiddleware — origins from settings.ALLOWED_ORIGINS
    # P0-5: add request-ID middleware for distributed tracing
    # Phase 1: include routers/routes.py and routers/risk.py

    return app

app = create_app()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

