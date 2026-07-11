# backend/tests/test_routing.py

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.services.routing as routing_module
from app.services.routing import _cache_key, _deduplicate_routes, _route_fingerprint, _sample_waypoints
from app.utils.cache import TTLCache

# ---------------------------------------------------------------------------
# Pure-function tests — no I/O, no mocking needed
# ---------------------------------------------------------------------------

def test_sample_waypoints_empty():
    """Empty coordinate list returns empty waypoints list."""
    assert _sample_waypoints([]) == []


def test_sample_waypoints_single_coordinate():
    """A single coordinate emits exactly one waypoint."""
    result = _sample_waypoints([[77.2, 28.6]])
    assert result == [(28.6, 77.2)]


def test_sample_waypoints_flips_lng_lat():
    """GeoJSON [lng, lat] is flipped to (lat, lng) in the output."""
    result = _sample_waypoints([[77.2090, 28.6139]])
    assert result[0] == (28.6139, 77.2090)


def test_sample_waypoints_interval():
    """A route longer than WAYPOINT_INTERVAL_M emits waypoints at ~100m intervals."""
    coords = [
        [77.2000, 28.6000],
        [77.2060, 28.6000],  # ~530m east at this latitude
    ]
    result = _sample_waypoints(coords)
    assert len(result) >= 2
    for lat, lng in result:
        assert 28.0 <= lat <= 30.0
        assert 76.0 <= lng <= 79.0


def test_route_fingerprint_empty():
    """Empty coordinate list returns an empty tuple."""
    assert _route_fingerprint([]) == ()


def test_route_fingerprint_rounds_to_3dp():
    """Coordinates are rounded to 3 decimal places in the fingerprint."""
    coords = [[77.20901, 28.61391], [77.21999, 28.62999]]
    fp = _route_fingerprint(coords)
    assert fp == ((77.209, 28.614), (77.22, 28.63))


def test_deduplicate_routes_removes_identical_geometry():
    """Two routes with the same coordinate list are deduplicated to one."""
    coords = [[77.2, 28.6], [77.21, 28.61], [77.22, 28.62]]
    def make():
        return {"geometry": {"coordinates": coords}, "duration_sec": 300.0, "distance_m": 1500.0, "waypoints": []}
    result = _deduplicate_routes([make(), make()])
    assert len(result) == 1


def test_deduplicate_routes_keeps_distinct_geometry():
    """Routes with different coordinate paths are both kept."""
    route_a = {"geometry": {"coordinates": [[77.2, 28.6], [77.21, 28.61]]}, "duration_sec": 300.0, "distance_m": 1500.0, "waypoints": []}  # noqa: E501
    route_b = {"geometry": {"coordinates": [[77.2, 28.6], [77.25, 28.65]]}, "duration_sec": 310.0, "distance_m": 1600.0, "waypoints": []}  # noqa: E501
    result = _deduplicate_routes([route_a, route_b])
    assert len(result) == 2


def test_deduplicate_routes_preserves_order():
    """The first occurrence of each unique route is kept (stable order)."""
    coords_a = [[77.2, 28.6], [77.21, 28.61]]
    coords_b = [[77.2, 28.6], [77.25, 28.65]]
    coords_c = [[77.2, 28.6], [77.21, 28.61]]  # duplicate of a
    route_a = {"geometry": {"coordinates": coords_a}, "duration_sec": 300.0, "distance_m": 1500.0, "waypoints": []}
    route_b = {"geometry": {"coordinates": coords_b}, "duration_sec": 400.0, "distance_m": 2000.0, "waypoints": []}
    route_c = {"geometry": {"coordinates": coords_c}, "duration_sec": 300.0, "distance_m": 1500.0, "waypoints": []}
    result = _deduplicate_routes([route_a, route_b, route_c])
    assert len(result) == 2
    assert result[0]["duration_sec"] == 300.0
    assert result[1]["duration_sec"] == 400.0


def test_cache_key_rounds_to_4dp():
    """_cache_key rounds coordinates to 4 decimal places."""
    key1 = _cache_key((28.61391, 77.20901), (28.70001, 77.30001), "balanced")
    key2 = _cache_key((28.61393, 77.20903), (28.70003, 77.30003), "balanced")
    assert key1 == key2


# ---------------------------------------------------------------------------
# TTLCache unit tests
# ---------------------------------------------------------------------------

def test_ttl_cache_miss_returns_none():
    cache = TTLCache(ttl_seconds=60)
    assert cache.get("missing") is None


def test_ttl_cache_hit_returns_value():
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", {"routes": []})
    assert cache.get("k") == {"routes": []}


def test_ttl_cache_expiry(monkeypatch):
    """After TTL elapses, get() returns None and clears the entry."""
    import time as _time
    cache = TTLCache(ttl_seconds=10)

    now = _time.monotonic()
    monkeypatch.setattr("app.utils.cache.time.monotonic", lambda: now)
    cache.set("k", "value")

    monkeypatch.setattr("app.utils.cache.time.monotonic", lambda: now + 11)
    assert cache.get("k") is None
    assert "k" not in cache._store


# ---------------------------------------------------------------------------
# GraphHopper integration tests (httpx mocked)
# ---------------------------------------------------------------------------

# GH response format: {"paths": [{"points": GeoJSON, "time": ms, "distance": m}]}
# "time" is milliseconds; routing.py divides by 1000.0 to get duration_sec.
_GH_RESPONSE = {
    "paths": [
        {
            "points": {
                "type": "LineString",
                "coordinates": [[77.2, 28.6], [77.21, 28.61]],
            },
            "time":     300_000,   # 300 seconds expressed in milliseconds
            "distance": 1500.0,   # already metres
        }
    ]
}


@pytest.mark.asyncio
async def test_get_routes_parses_gh_response():
    """get_routes correctly parses a GraphHopper response into the expected shape."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = _GH_RESPONSE
    mock_resp.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    routing_module._cache.clear()
    with patch("app.services.routing.httpx.AsyncClient", return_value=mock_client):
        # Both coords are within Delhi NCT bbox so the bounds guard passes.
        routes = await routing_module.get_routes((28.63, 77.21), (28.52, 77.09), profile="fastest")

    assert len(routes) == 1
    assert set(routes[0].keys()) == {"geometry", "duration_sec", "distance_m", "waypoints"}
    assert routes[0]["duration_sec"] == 300.0    # 300_000 ms ÷ 1000
    assert routes[0]["distance_m"]   == 1500.0
    assert isinstance(routes[0]["waypoints"], list)


@pytest.mark.asyncio
async def test_get_routes_cache_hit():
    """get_routes returns cached result without calling httpx when the cache is warm."""
    routing_module._cache.clear()

    fake_routes = [
        {"geometry": {}, "duration_sec": 100.0, "distance_m": 500.0, "waypoints": []}
    ]
    # WHY "balanced": SAFETY_PROFILE default is "balanced" in Phase 7.
    # get_routes() builds the cache key with "balanced"; using any other profile
    # string here causes a cache miss and the test falls through to httpx.
    key = _cache_key((28.6, 77.2), (28.7, 77.3), "balanced")
    routing_module._cache.set(key, fake_routes)

    with patch("app.services.routing.httpx.AsyncClient") as mock_cls:
        result = await routing_module.get_routes((28.6, 77.2), (28.7, 77.3))

    assert result == fake_routes
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_get_routes_unknown_profile_raises_value_error():
    """An unrecognised profile string raises ValueError before any HTTP call."""
    with pytest.raises(ValueError, match="Unknown profile"):
        await routing_module.get_routes((28.6, 77.2), (28.7, 77.3), profile="telepathy")


@pytest.mark.asyncio
async def test_get_routes_connect_error_raises_503():
    """ConnectError (GH container down) is converted to HTTPException(503)."""
    from fastapi import HTTPException as _HTTPException
    routing_module._cache.clear()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch("app.services.routing.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(_HTTPException) as exc_info:
            await routing_module.get_routes((28.63, 77.21), (28.52, 77.09), profile="fastest")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_get_routes_out_of_bounds_raises_value_error():
    """Coordinates outside Delhi NCT bbox raise ValueError before any HTTP call."""
    # Mumbai is far outside both lat and lng bounds — guaranteed to fail.
    with pytest.raises(ValueError, match="outside the Delhi NCT"):
        await routing_module.get_routes((19.07, 72.87), (28.6, 77.2), profile="fastest")
