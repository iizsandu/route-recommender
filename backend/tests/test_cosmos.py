"""
Tests for CosmosReadOnlyClient.

Strategy: mock CosmosClient.from_connection_string so no real Azure
connection is needed. All tests run offline in CI.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from azure.cosmos.exceptions import CosmosHttpResponseError

from app.services.cosmos_client import CosmosReadOnlyClient, _strip_metadata
from app.config import Settings


# WHY: simulates Cosmos's AsyncItemPaged — an async iterator — without
# needing a real SDK object. Any list of dicts becomes iterable this way.
async def _async_iter(items):
    for item in items:
        yield item


@pytest.fixture
def settings() -> Settings:
    # WHY: model_construct() skips Pydantic validation entirely.
    # Settings() would raise ValidationError because COSMOS_CONNECTION_STRING
    # has no default — we'd need a real env var in CI.
    return Settings.model_construct(
        COSMOS_CONNECTION_STRING="AccountEndpoint=https://fake.documents.azure.com:443/;AccountKey=ZmFrZQ==;",
        COSMOS_DATABASE_NAME="test_db",
        COSMOS_CONTAINER_NAME="test_crimes",
    )

# --- _strip_metadata unit tests (synchronous — no async needed) ---

def test_strip_metadata_removes_cosmos_fields():
    doc = {
        "id": "abc123",
        "crime_type": "Robbery",
        "_rid": "x1",
        "_self": "/dbs/x",
        "_etag": '"0000"',
        "_attachments": "attachments/",
        "_ts": 1700000000,
    }
    result = _strip_metadata(doc)
    assert result == {"id": "abc123", "crime_type": "Robbery"}


def test_strip_metadata_clean_doc_unchanged():
    # WHY: if no Cosmos fields exist, the function must be a no-op
    doc = {"id": "abc123", "lat": 28.6, "lng": 77.2}
    assert _strip_metadata(doc) == doc


# --- Guard: calling fetch before connect() ---

@pytest.mark.asyncio
async def test_fetch_before_connect_raises(settings):
    client = CosmosReadOnlyClient(settings)
    # WHY: _client is None at construction — we want RuntimeError with a
    # clear message, not AttributeError: 'NoneType' has no attribute '...'
    with pytest.raises(RuntimeError, match="connect()"):
        await client.fetch_crime_records()

@pytest.mark.asyncio
async def test_fetch_returns_stripped_records(settings):
    raw_docs = [
        {"id": "1", "crime_type": "Robbery", "_rid": "r1", "_ts": 1700000000},
        {"id": "2", "crime_type": "Assault", "_rid": "r2", "_ts": 1700000001},
    ]

    mock_container = MagicMock()
    # WHY: return_value, not side_effect — query_items returns an iterable,
    # it doesn't raise; the iterable yields items when looped over
    mock_container.query_items.return_value = _async_iter(raw_docs)

    mock_db = MagicMock()
    mock_db.get_container_client.return_value = mock_container

    mock_cosmos_client = MagicMock()
    mock_cosmos_client.get_database_client.return_value = mock_db

    with patch(
        "app.services.cosmos_client.CosmosClient.from_connection_string",
        return_value=mock_cosmos_client,
    ):
        client = CosmosReadOnlyClient(settings)
        await client.connect()
        records = await client.fetch_crime_records()

    assert len(records) == 2
    assert records[0] == {"id": "1", "crime_type": "Robbery"}   # business fields present
    assert records[1] == {"id": "2", "crime_type": "Assault"}
    assert "_rid" not in records[0]   # WHY: explicit — this is the whole point of _strip_metadata
    assert "_ts" not in records[0]

@pytest.mark.asyncio
async def test_fetch_empty_result(settings):
    mock_container = MagicMock()
    mock_container.query_items.return_value = _async_iter([])
    mock_db = MagicMock()
    mock_db.get_container_client.return_value = mock_container
    mock_cosmos_client = MagicMock()
    mock_cosmos_client.get_database_client.return_value = mock_db

    with patch(
        "app.services.cosmos_client.CosmosClient.from_connection_string",
        return_value=mock_cosmos_client,
    ):
        client = CosmosReadOnlyClient(settings)
        await client.connect()
        records = await client.fetch_crime_records()

    assert records == []


@pytest.mark.asyncio
async def test_fetch_cosmos_error_reraises(settings):
    # WHY: real exception instance, not a MagicMock — Python only allows
    # raising BaseException subclasses; MagicMock cannot be raised
    mock_error = CosmosHttpResponseError(status_code=429, message="Too Many Requests")

    # WHY: async generator that raises on first iteration — simulates
    # Cosmos returning a 429 mid-page rather than before the call
    async def _failing_iter(*args, **kwargs):
        raise mock_error
        yield  # WHY: yield makes this an async generator, required for async for

    mock_container = MagicMock()
    mock_container.query_items.return_value = _failing_iter()
    mock_db = MagicMock()
    mock_db.get_container_client.return_value = mock_container
    mock_cosmos_client = MagicMock()
    mock_cosmos_client.get_database_client.return_value = mock_db

    with patch(
        "app.services.cosmos_client.CosmosClient.from_connection_string",
        return_value=mock_cosmos_client,
    ):
        client = CosmosReadOnlyClient(settings)
        await client.connect()
        with pytest.raises(CosmosHttpResponseError):
            await client.fetch_crime_records()


@pytest.mark.asyncio
async def test_fetch_since_uses_timestamp_filter(settings):
    mock_container = MagicMock()
    mock_container.query_items.return_value = _async_iter([])
    mock_db = MagicMock()
    mock_db.get_container_client.return_value = mock_container
    mock_cosmos_client = MagicMock()
    mock_cosmos_client.get_database_client.return_value = mock_db

    with patch(
        "app.services.cosmos_client.CosmosClient.from_connection_string",
        return_value=mock_cosmos_client,
    ):
        client = CosmosReadOnlyClient(settings)
        await client.connect()
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await client.fetch_crime_records(since=since)

    call_kwargs = mock_container.query_items.call_args.kwargs
    # WHY: assert the WHERE clause was used, not SELECT *
    assert "@since" in call_kwargs["query"]
    # WHY: assert the timestamp value is correct — conversion bug would show here
    assert call_kwargs["parameters"][0]["value"] == int(since.timestamp())

