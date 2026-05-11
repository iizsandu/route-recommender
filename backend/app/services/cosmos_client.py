"""
Read-only async Cosmos DB client.

Only fetch_crime_records() is exposed. No write methods exist on this class.
RBAC credentials (read-only key) enforce the read-only constraint at the
network level; this module enforces it at the code level.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError

from app.config import Settings

logger = logging.getLogger(__name__)

_COSMOS_INTERNAL_FIELDS = frozenset({
    "_rid", "_self", "_etag", "_attachments", "_ts"
})

def _strip_metadata(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in _COSMOS_INTERNAL_FIELDS}

class CosmosReadOnlyClient:
    """
    Async wrapper around the Azure Cosmos SDK.
    Exposes only read operations — write methods are intentionally absent.
    """

    def __init__(self, settings:Settings) -> None:
        self._settings = settings
        self._client: CosmosClient | None = None

    async def connect(self) -> None:
        self._client = CosmosClient.from_connection_string(
            self._settings.COSMOS_CONNECTION_STRING
        )
        logger.info("Cosmos async client connected")

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
            logger.info("Cosmos async client closed")

    async def fetch_crime_records(
            self, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """
        Fetch crime records from Cosmos, stripping internal metadata.
        Pass since= to load only records written after that datetime.
        """

        if self._client is None:
            raise RuntimeError(
                "CosmosReadOnlyClient.connect() must be called before fetching records"
            )
        
        database = self._client.get_database_client(self._settings.COSMOS_DATABASE_NAME)
        container = database.get_container_client(self._settings.COSMOS_CONTAINER_NAME)
        
        if since is not None:
            since_ts = int(since.replace(tzinfo=timezone.utc).timestamp())
            query = "SELECT * FROM c WHERE c._ts >= @since"
            parameters: list[dict[str, Any]] = [{"name": "@since", "value": since_ts}]
        else:
            query = "SELECT * FROM c"
            parameters = []

        records: list[dict[str, Any]] = []

        try:
            async for item in container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ):
                records.append(_strip_metadata(item))
        
        except CosmosHttpResponseError as exc:
            logger.error(
                "Cosmos query failed",
                # WHY: "message" is reserved by logging.LogRecord — using it
                # in extra raises KeyError at runtime
                extra={"status_code": exc.status_code, "error_message": exc.message},
            )
            raise  # WHY: re-raise so FastAPI returns 500, not a silent empty list

        logger.info("Fetched %d crime records from Cosmos", len(records))
        return records
