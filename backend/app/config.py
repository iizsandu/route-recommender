"""
Centralised configuration for the Route Recommender backend.

Every environment variable used anywhere in the app must be declared here.
Fields with no default are REQUIRED — the service raises ValidationError
at import time if they are absent, preventing silent misconfiguration.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Cosmos DB (read-only) ---
    # WHY: no default — service refuses to start if this env var is absent
    COSMOS_CONNECTION_STRING: str
    COSMOS_DATABASE_NAME: str = "route_recommender"
    COSMOS_CONTAINER_NAME: str = "structured_crimes"

    # --- OpenRouteService ---
    # WHY: no default — service refuses to start if this env var is absent
    ORS_API_KEY: str
    ORS_BASE_URL: str = "https://api.openrouteservice.org"

    # --- MLflow ---
    MLFLOW_TRACKING_URI: str = "sqlite:///ml/artifacts/mlruns.db"
    MLFLOW_REGISTRY_URI: str = "sqlite:///ml/artifacts/mlruns.db"

    # --- CORS ---
    # WHY: stored as a comma-separated string in env; split to list at usage
    # site rather than here, to keep the Settings model serialisable to JSON
    ALLOWED_ORIGINS: str = "http://localhost:3000,https://route-recommender-web.vercel.app"

    # --- Logging ---
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # WHY: Literal type means Pydantic validates the value against the enum
    # at startup — typo "jsn" fails fast instead of producing silent no-op
    LOG_FORMAT: Literal["json", "console"] = "console"

    # TODO (Phase 1 — ADR #4): add crime_type_weights dict here
    # TODO (Phase 1 — ADR #5): add time_of_day_multipliers dict here
