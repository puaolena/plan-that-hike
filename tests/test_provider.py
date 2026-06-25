import pytest
import asyncio
from unittest.mock import patch, MagicMock
from pydantic import ValidationError
from backend.provider import (
    TrailFactsRequest,
    WeatherDataRequest,
    TrailFactsResponse,
    WeatherDataResponse,
    _search_mock_database,
    _search_mock_weather,
    _apply_simulation_triggers,
    _mock_get_trail_facts,
    _mock_get_weather_data,
    get_trail_facts,
    get_weather_data,
    TrailFactsProvider,
)

# 1. Pydantic input validation tests
def test_trail_facts_request_validation():
    # Valid query
    req = TrailFactsRequest(destination="Mission Peak")
    assert req.destination == "Mission Peak"
    
    # Destination gets stripped
    req_strip = TrailFactsRequest(destination="  Henry Cowell  ")
    assert req_strip.destination == "Henry Cowell"
    
    # Empty destination raises ValidationError
    with pytest.raises(ValidationError):
        TrailFactsRequest(destination="")
        
    # Whitespace gets stripped to empty
    req_space = TrailFactsRequest(destination="   ")
    assert req_space.destination == ""

def test_weather_data_request_validation():
    # Valid coordinates
    req = WeatherDataRequest(lat=37.5, lon=-122.0)
    assert req.lat == 37.5
    assert req.lon == -122.0
    
    # Latitude out of bounds
    with pytest.raises(ValidationError):
        WeatherDataRequest(lat=91.0, lon=-122.0)
    with pytest.raises(ValidationError):
        WeatherDataRequest(lat=-90.1, lon=-122.0)
        
    # Longitude out of bounds
    with pytest.raises(ValidationError):
        WeatherDataRequest(lat=37.5, lon=181.0)
    with pytest.raises(ValidationError):
        WeatherDataRequest(lat=37.5, lon=-180.1)

# 2. Internal mock search function tests
def test_search_mock_database():
    # Exact match (case insensitive/stripped)
    res = _search_mock_database("mission peak")
    assert res is not None
    assert "Stanford Trail" in res["name"]
    
    # Substring match
    res_sub = _search_mock_database("Henry")
    assert res_sub is not None
    assert "Henry Cowell" in res_sub["name"]
    
    # Ambiguous match (e.g., query that matches multiple, wait, actually 'yosemite' or 'peak' matches both)
    # _MOCK_TRAILS has 'mission peak' and 'yosemite eagle peak'. So 'peak' should be ambiguous.
    res_ambig = _search_mock_database("peak")
    assert res_ambig is not None
    assert res_ambig["ambiguous"] is True
    assert len(res_ambig["suggestions"]) >= 2
    
    # Fuzzy match (e.g., 'mision peek')
    res_fuzzy = _search_mock_database("mision peek")
    assert res_fuzzy is not None
    assert "Stanford Trail" in res_fuzzy["name"]
    
    # Completely unknown
    res_unknown = _search_mock_database("nonexistent trail 12345")
    assert res_unknown is None

def test_search_mock_weather():
    # Mission Peak coordinates: lat=37.5, lon=-121.9
    res = _search_mock_weather(37.5, -121.9)
    assert res is not None
    assert res["temperature_f"] == 78.0
    
    # Unknown coordinates
    res_unknown = _search_mock_weather(0.0, 0.0)
    assert res_unknown is None

# 3. Simulation triggers and mock responses
@pytest.mark.asyncio
async def test_mock_get_trail_facts():
    # 1. Standard trail
    res = await _mock_get_trail_facts("Mission Peak")
    assert res.found is True
    assert "Stanford Trail" in res.name
    assert res.source == "Mock Database"
    assert res.weather_status == "success"
    
    # 2. Unknown trail
    res_unknown = await _mock_get_trail_facts("Unknown Trail ABC")
    assert res_unknown.found is False
    assert res_unknown.name == "Unknown Trail ABC"
    assert res_unknown.source == "Manual Setup"
    
    # 3. "missing" trigger
    res_missing = await _mock_get_trail_facts("Mission Peak missing")
    assert res_missing.found is False
    assert res_missing.trail_status == "missing"
    assert res_missing.weather_status == "failed"
    
    # 4. "fail" trigger
    res_fail = await _mock_get_trail_facts("Mission Peak fail")
    assert res_fail.found is True
    assert res_fail.weather_status == "failed"
    assert "no match" in res_fail.weather_error
    
    # 5. "stale" trigger
    res_stale = await _mock_get_trail_facts("Mission Peak stale")
    assert res_stale.found is True
    assert res_stale.weather_status == "success"
    
    # 6. "timeout" trigger (retries)
    # attempt < 3: status is timeout
    res_timeout = await _mock_get_trail_facts("Mission Peak timeout", attempt=1)
    assert res_timeout.weather_status == "timeout"
    assert "timeout" in res_timeout.weather_error
    
    # attempt >= 3: status succeeds
    res_timeout_retry = await _mock_get_trail_facts("Mission Peak timeout", attempt=3)
    assert res_timeout_retry.weather_status == "success"

def test_mock_get_weather_data():
    # Known coords
    res = _mock_get_weather_data(37.5, -121.9)
    assert res.status == "success"
    assert res.temperature_f == 78.0
    
    # Unknown coords
    res_unknown = _mock_get_weather_data(0.0, 0.0)
    assert res_unknown.status == "failed"
    assert "No mock weather data" in res_unknown.error

# 4. Public API & Legacy wrapper test
@pytest.mark.asyncio
async def test_public_api_mock_mode():
    # When USE_REAL_TIME_DATA is False (default)
    with patch("backend.provider.USE_REAL_TIME_DATA", False):
        res_trail = await get_trail_facts("Henry Cowell")
        assert res_trail.found is True
        assert "Redwood State Park" in res_trail.name
        
        res_weather = await get_weather_data(37.1, -122.0)
        assert res_weather.status == "success"
        assert res_weather.temperature_f == 62.0

@pytest.mark.asyncio
async def test_legacy_provider_shim():
    provider = TrailFactsProvider()
    
    # Check search_mock_database delegate
    db_res = provider.search_mock_database("mission peak")
    assert db_res is not None
    assert "Stanford Trail" in db_res["name"]
    
    # Check async get_trail_facts method returning dict
    res = await provider.get_trail_facts("Mission Peak")
    assert isinstance(res, dict)
    assert res["found"] is True
    assert "Stanford Trail" in res["name"]
