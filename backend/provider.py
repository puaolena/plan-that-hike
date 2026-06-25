"""
provider.py — Structured data gateway for the hiking trail assistant.

Environment flag
----------------
USE_REAL_TIME_DATA (bool, default False)
    False → return structured mock data for the three canonical trails.
    True  → call real external / Antigravity-agent data sources (stubs
             ready for tool integration).

Public API
----------
get_trail_facts(destination: str) -> TrailFactsResponse
get_weather_data(lat: float, lon: float)  -> WeatherDataResponse
"""

import asyncio
import datetime
import difflib
import logging
import os
import re
from typing import Any, Dict, List, Optional

import pydantic
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
from pydantic import Field, field_validator

# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

load_dotenv()

# Parse USE_REAL_TIME_DATA as a boolean — accepts "true" / "1" / "yes".
# Default is intentionally "false" (mock mode) so the app works safely
# without any API quota consumption unless explicitly opted in.
_raw_flag: str = os.environ.get("USE_REAL_TIME_DATA", "false")
USE_REAL_TIME_DATA: bool = _raw_flag.strip().lower() in {"1", "true", "yes"}

GEMINI_API_KEY: Optional[str] = os.environ.get("GEMINI_API_KEY")

# ---------------------------------------------------------------------------
# Pydantic I/O schemas
# ---------------------------------------------------------------------------


class TrailFactsRequest(pydantic.BaseModel):
    """Validated input for get_trail_facts."""

    destination: str = Field(..., min_length=1, description="Human-readable trail name or location query.")

    @field_validator("destination")
    @classmethod
    def strip_destination(cls, v: str) -> str:
        return v.strip()


class WeatherDataRequest(pydantic.BaseModel):
    """Validated input for get_weather_data."""

    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees.")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees.")


class TrailFactsResponse(pydantic.BaseModel):
    """Structured output returned by get_trail_facts."""

    name: str
    distance_miles: float = Field(..., ge=0.0)
    elevation_gain_feet: float = Field(..., ge=0.0)
    trailhead_location: str
    weather_forecast: str
    sunrise: str
    sunset: str
    terrain: str

    # Retrieval metadata
    found: bool
    ambiguous: bool = False
    suggestions: List[str] = Field(default_factory=list)
    source: str = "Mock Database"

    # Weather status fields (populated by provider logic)
    weather_status: str = "success"       # "success" | "failed" | "timeout"
    weather_timestamp: str = ""
    weather_error: str = ""

    # Optional simulation marker
    trail_status: Optional[str] = None


class WeatherDataResponse(pydantic.BaseModel):
    """Structured output returned by get_weather_data."""

    lat: float
    lon: float
    temperature_f: float
    condition: str
    wind_mph: float
    humidity_pct: float
    sunrise: str
    sunset: str
    source: str = "Mock Weather Service"
    fetched_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    status: str = "success"    # "success" | "failed"
    error: str = ""


class AITrailFacts(pydantic.BaseModel):
    """Schema used when the Antigravity agent returns structured trail data."""

    name: str
    distance_miles: float
    elevation_gain_feet: float
    trailhead_location: str
    weather_forecast: str
    sunrise: str
    sunset: str
    terrain: str
    found: bool

# ---------------------------------------------------------------------------
# Mock data store
# ---------------------------------------------------------------------------

_MOCK_TRAILS: Dict[str, Dict[str, Any]] = {
    "mission peak": {
        "name": "Mission Peak (Stanford Trail)",
        "distance_miles": 6.0,
        "elevation_gain_feet": 2100.0,
        "trailhead_location": "Stanford Ave Trailhead, Fremont, CA",
        "weather_forecast": "Sunny, 78°F, low wind (high exposure, no shade)",
        "sunrise": "5:45 AM",
        "sunset": "8:30 PM",
        "terrain": "Steep, rocky gravel road, completely exposed",
        "ambiguous": False,
        "suggestions": [],
        "found": True,
        "source": "Mock Database",
    },
    "henry cowell": {
        "name": "Henry Cowell Redwood State Park (Pipeline Loop)",
        "distance_miles": 3.5,
        "elevation_gain_feet": 200.0,
        "trailhead_location": "Henry Cowell Main Entrance, Felton, CA",
        "weather_forecast": "Shaded redwoods, cool/overcast, 62°F, calm",
        "sunrise": "5:50 AM",
        "sunset": "8:32 PM",
        "terrain": "Flat, soft dirt trail, heavily shaded, minor tree roots",
        "ambiguous": False,
        "suggestions": [],
        "found": True,
        "source": "Mock Database",
    },
    "yosemite eagle peak": {
        "name": "Yosemite Eagle Peak",
        "distance_miles": 12.0,
        "elevation_gain_feet": 3500.0,
        "trailhead_location": "Camp 4 Trailhead, Yosemite Valley, CA",
        "weather_forecast": "Clear sky, 68°F at valley floor, cooler (55°F) with potential wind at summit",
        "sunrise": "5:40 AM",
        "sunset": "8:25 PM",
        "terrain": "Very steep, rocky switchbacks, high elevation (7,779 ft summit), slippery granite sections",
        "ambiguous": False,
        "suggestions": [],
        "found": True,
        "source": "Mock Database",
    },
}

# Approximate coordinates for mock weather lookup (keyed to mock trail names)
_MOCK_WEATHER_BY_COORDS: List[Dict[str, Any]] = [
    {   # Mission Peak / Fremont
        "lat_range": (37.4, 37.6), "lon_range": (-122.0, -121.8),
        "temperature_f": 78.0, "condition": "Sunny", "wind_mph": 5.0,
        "humidity_pct": 35.0, "sunrise": "5:45 AM", "sunset": "8:30 PM",
    },
    {   # Henry Cowell / Felton / Santa Cruz
        "lat_range": (37.0, 37.2), "lon_range": (-122.1, -121.9),
        "temperature_f": 62.0, "condition": "Overcast / Shaded",
        "wind_mph": 2.0, "humidity_pct": 80.0,
        "sunrise": "5:50 AM", "sunset": "8:32 PM",
    },
    {   # Yosemite Valley
        "lat_range": (37.7, 37.9), "lon_range": (-119.7, -119.5),
        "temperature_f": 68.0, "condition": "Clear",
        "wind_mph": 10.0, "humidity_pct": 25.0,
        "sunrise": "5:40 AM", "sunset": "8:25 PM",
    },
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _search_mock_database(query: str) -> Optional[Dict[str, Any]]:
    """Return a copy of the matching mock trail dict, or None if not found."""
    q = query.lower().strip()

    # 1. Exact key match
    if q in _MOCK_TRAILS:
        return dict(_MOCK_TRAILS[q])

    # 2. Substring match
    matches = [k for k in _MOCK_TRAILS if k in q or q in k]
    if len(matches) == 1:
        return dict(_MOCK_TRAILS[matches[0]])
    if len(matches) > 1:
        return {
            "name": query,
            "distance_miles": 0.0,
            "elevation_gain_feet": 0.0,
            "trailhead_location": "Unknown",
            "weather_forecast": "Unknown",
            "sunrise": "6:00 AM",
            "sunset": "8:00 PM",
            "terrain": "Unknown",
            "ambiguous": True,
            "suggestions": [_MOCK_TRAILS[m]["name"] for m in matches],
            "found": False,
            "source": "Mock Database (Ambiguous)",
        }

    # 3. Fuzzy match
    close = difflib.get_close_matches(q, _MOCK_TRAILS.keys(), n=1, cutoff=0.5)
    if close:
        return dict(_MOCK_TRAILS[close[0]])

    return None


def _search_mock_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Return mock weather for coordinates that fall within a known bounding box."""
    for entry in _MOCK_WEATHER_BY_COORDS:
        lat_lo, lat_hi = entry["lat_range"]
        lon_lo, lon_hi = entry["lon_range"]
        if lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi:
            return {k: v for k, v in entry.items() if not k.endswith("_range")}
    return None


def _apply_simulation_triggers(
    facts: Dict[str, Any],
    sim_trigger: Optional[str],
    attempt: int,
) -> Dict[str, Any]:
    """Mutate *facts* in-place to inject simulation artefacts, then return it."""
    now = datetime.datetime.now(datetime.timezone.utc)

    if sim_trigger == "timeout":
        if attempt < 3:
            facts["weather_status"] = "timeout"
            facts["weather_forecast"] = ""
            facts["weather_timestamp"] = ""
            facts["weather_error"] = f"Simulated weather service timeout (Attempt {attempt} failed)"
        else:
            facts["weather_status"] = "success"
            facts["weather_timestamp"] = (now - datetime.timedelta(minutes=2)).isoformat()
            facts["weather_error"] = ""
    elif sim_trigger == "fail":
        facts["weather_status"] = "failed"
        facts["weather_forecast"] = ""
        facts["weather_timestamp"] = ""
        facts["weather_error"] = "Weather service returned no match (404)"
    elif sim_trigger == "stale":
        facts["weather_status"] = "success"
        facts["weather_timestamp"] = (now - datetime.timedelta(hours=4)).isoformat()
        facts["weather_error"] = ""
    else:
        facts["weather_status"] = "success" if facts.get("weather_forecast") else "failed"
        facts["weather_timestamp"] = (now - datetime.timedelta(minutes=5)).isoformat()
        facts["weather_error"] = ""

    return facts

# ---------------------------------------------------------------------------
# Mock-mode implementations
# ---------------------------------------------------------------------------


async def _mock_get_trail_facts(
    destination: str,
    attempt: int = 1,
) -> TrailFactsResponse:
    """Return trail facts from the in-memory mock database (no external calls)."""
    norm = destination.lower().strip()

    # --- "missing" simulation keyword ---
    if "missing" in norm:
        clean_name = destination.lower().replace("missing", "").strip() or "Custom Hike"
        raw: Dict[str, Any] = {
            "name": clean_name,
            "distance_miles": 0.0,
            "elevation_gain_feet": 0.0,
            "trailhead_location": "",
            "weather_forecast": "",
            "sunrise": "6:00 AM",
            "sunset": "8:00 PM",
            "terrain": "",
            "ambiguous": False,
            "suggestions": [],
            "found": False,
            "trail_status": "missing",
            "weather_status": "failed",
            "weather_timestamp": "",
            "weather_error": "",
            "source": "Manual Setup",
        }
        return TrailFactsResponse(**raw)

    # --- Extract simulation trigger keyword ---
    sim_trigger: Optional[str] = None
    clean_dest = destination
    for trigger in ("timeout", "fail", "stale"):
        if trigger in norm:
            sim_trigger = trigger
            clean_dest = re.sub(trigger, "", destination, flags=re.IGNORECASE).strip()
            break

    # --- Simulate async latency for timeout path ---
    if sim_trigger == "timeout":
        await asyncio.sleep(0.6)

    # --- Search mock database ---
    facts = _search_mock_database(clean_dest)

    # --- Blank fallback for completely unknown trails ---
    if not facts:
        facts = {
            "name": clean_dest,
            "distance_miles": 0.0,
            "elevation_gain_feet": 0.0,
            "trailhead_location": "",
            "weather_forecast": "",
            "sunrise": "6:00 AM",
            "sunset": "8:00 PM",
            "terrain": "",
            "ambiguous": False,
            "suggestions": [],
            "found": False,
            "source": "Manual Setup",
        }

    _apply_simulation_triggers(facts, sim_trigger, attempt)
    return TrailFactsResponse(**facts)


def _mock_get_weather_data(lat: float, lon: float) -> WeatherDataResponse:
    """Return weather data from the coordinate-keyed mock store."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entry = _search_mock_weather(lat, lon)

    if entry:
        return WeatherDataResponse(
            lat=lat,
            lon=lon,
            temperature_f=entry["temperature_f"],
            condition=entry["condition"],
            wind_mph=entry["wind_mph"],
            humidity_pct=entry["humidity_pct"],
            sunrise=entry["sunrise"],
            sunset=entry["sunset"],
            source="Mock Weather Service",
            fetched_at=now_iso,
            status="success",
            error="",
        )

    # Coordinates outside any known bounding box
    return WeatherDataResponse(
        lat=lat,
        lon=lon,
        temperature_f=0.0,
        condition="Unknown",
        wind_mph=0.0,
        humidity_pct=0.0,
        sunrise="6:00 AM",
        sunset="8:00 PM",
        source="Mock Weather Service",
        fetched_at=now_iso,
        status="failed",
        error=f"No mock weather data available for coordinates ({lat}, {lon}).",
    )

# ---------------------------------------------------------------------------
# Real-time placeholder implementations
# (stubs — ready for Antigravity agent tool integration)
# ---------------------------------------------------------------------------


async def _realtime_get_trail_facts(destination: str, attempt: int = 1) -> TrailFactsResponse:
    """
    Retrieve live trail data via the Antigravity agent using Brave Search.
    """
    from google.antigravity import Agent, LocalAgentConfig, types  # noqa: PLC0415

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Cannot execute real-time trail data fetch."
        )

    mcp_servers = [
        types.McpStdioServer(
            name="brave-search",
            command="/opt/homebrew/bin/npx",
            args=["-y", "@modelcontextprotocol/server-brave-search"]
        )
    ]

    config = LocalAgentConfig(
        api_key=GEMINI_API_KEY,
        mcp_servers=mcp_servers,
        response_schema=AITrailFacts,
        system_instructions=(
            "You are an expert hiking guide assistant. Retrieve realistic, typical trail facts "
            "for the requested day hike. You have access to the brave-search MCP server tools. "
            "Search the web to find accurate, typical values for distance in miles, elevation gain in feet, "
            "trailhead location, typical weather, sunrise/sunset, and terrain description. "
            "If the trail is fictional or cannot be identified, set found=False."
        ),
    )

    now = datetime.datetime.now(datetime.timezone.utc)

    try:
        async with Agent(config=config) as agent:
            prompt = f"Retrieve objective trail facts for: {destination}"
            response = await agent.chat(prompt)
            ai_data: Optional[Dict[str, Any]] = await response.structured_output()

        if ai_data and ai_data.get("found"):
            return TrailFactsResponse(
                name=ai_data.get("name", destination),
                distance_miles=ai_data.get("distance_miles", 0.0),
                elevation_gain_feet=ai_data.get("elevation_gain_feet", 0.0),
                trailhead_location=ai_data.get("trailhead_location", "Unknown"),
                weather_forecast=ai_data.get("weather_forecast", "Unknown"),
                sunrise=ai_data.get("sunrise", "6:00 AM"),
                sunset=ai_data.get("sunset", "8:00 PM"),
                terrain=ai_data.get("terrain", "Unknown"),
                found=True,
                ambiguous=False,
                suggestions=[],
                source="Antigravity Agent with Brave Search (Real-Time)",
                weather_status="success",
                weather_timestamp=(now - datetime.timedelta(minutes=1)).isoformat(),
                weather_error="",
            )
    except Exception as e:
        logger.error("[REALTIME-TRAIL] Failed: %s", e)

    # Fallback
    return TrailFactsResponse(
        name=destination,
        distance_miles=0.0,
        elevation_gain_feet=0.0,
        trailhead_location="",
        weather_forecast="",
        sunrise="6:00 AM",
        sunset="8:00 PM",
        terrain="",
        found=False,
        source="Antigravity Agent with Brave Search (Real-Time)",
        weather_status="failed",
        weather_timestamp=now.isoformat(),
        weather_error="Agent could not identify or search for the trail.",
    )


async def _realtime_get_weather_data(lat: float, lon: float) -> WeatherDataResponse:
    """
    Retrieve live weather data via the Antigravity agent using OpenWeather.
    """
    from google.antigravity import Agent, LocalAgentConfig, types  # noqa: PLC0415

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Cannot execute real-time weather fetch."
        )

    openweather_api_key = os.environ.get("OPENWEATHER_API_KEY")
    if not openweather_api_key:
        raise RuntimeError(
            "OPENWEATHER_API_KEY is not set. Cannot execute real-time weather fetch."
        )

    mcp_servers = [
        types.McpStdioServer(
            name="weather-service",
            command="/Users/olena/anaconda3/bin/mcp-server-weather",
            args=["--api_key", openweather_api_key]
        )
    ]

    config = LocalAgentConfig(
        api_key=GEMINI_API_KEY,
        mcp_servers=mcp_servers,
        response_schema=WeatherDataResponse,
        system_instructions=(
            "You are a weather service assistant. Use the connected weather-service MCP server "
            "to retrieve the weather for the given lat/lon coordinates. Because the weather tool "
            "(`query_weather`) requires a city name, you must first determine the closest English "
            "city name for the coordinates, then run the query_weather tool. Convert the response metrics "
            "if necessary to Celsius/Fahrenheit, but return it in Fahrenheit as required by the schema."
        ),
    )

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        async with Agent(config=config) as agent:
            prompt = f"Retrieve weather data for coordinates lat={lat}, lon={lon}."
            response = await agent.chat(prompt)
            ai_data: Optional[Dict[str, Any]] = await response.structured_output()

        if ai_data:
            return WeatherDataResponse(
                lat=lat,
                lon=lon,
                temperature_f=ai_data.get("temperature_f", 0.0),
                condition=ai_data.get("condition", "Unknown"),
                wind_mph=ai_data.get("wind_mph", 0.0),
                humidity_pct=ai_data.get("humidity_pct", 0.0),
                sunrise=ai_data.get("sunrise", "6:00 AM"),
                sunset=ai_data.get("sunset", "8:00 PM"),
                source="OpenWeather API via MCP",
                fetched_at=now_iso,
                status="success",
                error="",
            )
    except Exception as e:
        logger.error("[REALTIME-WEATHER] Failed: %s", e)
        return WeatherDataResponse(
            lat=lat,
            lon=lon,
            temperature_f=0.0,
            condition="Error",
            wind_mph=0.0,
            humidity_pct=0.0,
            sunrise="6:00 AM",
            sunset="8:00 PM",
            source="OpenWeather API via MCP",
            fetched_at=now_iso,
            status="failed",
            error=str(e),
        )

    return WeatherDataResponse(
        lat=lat,
        lon=lon,
        temperature_f=0.0,
        condition="Not Found",
        wind_mph=0.0,
        humidity_pct=0.0,
        sunrise="6:00 AM",
        sunset="8:00 PM",
        source="OpenWeather API via MCP",
        fetched_at=now_iso,
        status="failed",
        error="No weather data generated by the agent.",
    )

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_trail_facts(
    destination: str,
    attempt: int = 1,
) -> TrailFactsResponse:
    """
    Return structured trail facts for *destination*.

    Parameters
    ----------
    destination:
        Human-readable trail name or location query.
    attempt:
        Retry counter forwarded to simulation logic (mock mode only).

    Returns
    -------
    TrailFactsResponse
        Fully validated Pydantic model with trail data and weather metadata.

    Raises
    ------
    pydantic.ValidationError
        If *destination* fails input validation.
    RuntimeError
        (Real-time mode only) If GEMINI_API_KEY is missing.
    """
    # Validate input
    req = TrailFactsRequest(destination=destination)

    if USE_REAL_TIME_DATA:
        return await _realtime_get_trail_facts(req.destination, attempt=attempt)
    return await _mock_get_trail_facts(req.destination, attempt=attempt)


async def get_weather_data(lat: float, lon: float) -> WeatherDataResponse:
    """
    Return structured weather data for the given coordinates.

    Parameters
    ----------
    lat:
        Latitude in decimal degrees (−90 to 90).
    lon:
        Longitude in decimal degrees (−180 to 180).

    Returns
    -------
    WeatherDataResponse
        Fully validated Pydantic model with weather conditions.

    Raises
    ------
    pydantic.ValidationError
        If *lat* or *lon* are out of valid range.
    """
    # Validate input
    req = WeatherDataRequest(lat=lat, lon=lon)

    if USE_REAL_TIME_DATA:
        return await _realtime_get_weather_data(req.lat, req.lon)
    return _mock_get_weather_data(req.lat, req.lon)


# ---------------------------------------------------------------------------
# Legacy compatibility shim
# (preserves the TrailFactsProvider class used in earlier code versions)
# ---------------------------------------------------------------------------


class TrailFactsProvider:
    """
    Thin wrapper kept for backward-compatibility with callers that
    instantiate TrailFactsProvider directly.

    New code should call the module-level get_trail_facts() / get_weather_data()
    functions directly.
    """

    def __init__(self) -> None:
        pass

    def search_mock_database(self, query: str) -> Optional[Dict[str, Any]]:
        """Delegate to the module-level helper."""
        return _search_mock_database(query)

    async def get_trail_facts(
        self,
        destination: str,
        api_key: Optional[str] = None,
        attempt: int = 1,
    ) -> Dict[str, Any]:
        """
        Async trail facts retrieval, maintaining the old dict-return contract.

        *api_key* is accepted for backward-compatibility but is no longer used
        directly; set GEMINI_API_KEY in the environment instead.
        """
        result = await get_trail_facts(destination, attempt=attempt)
        return result.model_dump()
