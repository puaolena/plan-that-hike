import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Load .env before importing modules that read env vars at import time
load_dotenv()

from backend.agent import HikePlanningOrchestrator, generate_packing_list  # noqa: E402

app = FastAPI(title="Pack for This Hike API")

# Configure a strict CORSMiddleware configuration block to safely accept inbound requests
# Allowing localhost and any origins (to support fetching from local file:/// workspace files or dev servers)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class FetchTrailRequest(BaseModel):
    destination: str
    api_key: Optional[str] = None
    # lat/lon are optional — when provided the Weather sub-agent is dispatched
    # and richer weather data is merged into the trail facts response.
    lat: Optional[float] = None
    lon: Optional[float] = None
    # `attempt` is kept for frontend backward-compatibility; the orchestrator
    # handles retries internally via the provider layer.
    attempt: Optional[int] = 1


class GeneratePackingRequest(BaseModel):
    facts: Dict[str, Any]
    answers: Dict[str, Any]
    api_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/check-key")
async def check_api_key():
    """Check whether a GEMINI_API_KEY is configured on the server."""
    return {"has_key": bool(os.getenv("GEMINI_API_KEY"))}


@app.post("/api/fetch-trail-data")
@app.post("/api/fetch-trail")
async def fetch_trail(request: FetchTrailRequest):
    """
    Fetch trail facts (and optionally weather) via the HikePlanningOrchestrator.

    Runs the full Plan → Act → Observe loop:
      - TrailFacts sub-agent is always dispatched.
      - Weather sub-agent is dispatched only when lat/lon are provided.

    The response keeps all existing flat trail-facts fields at the top level
    so the frontend requires no changes. Supplementary orchestration fields
    (weather_data, observations, errors, use_real_time_data) are appended.

    The active data mode (mock vs real-time) is controlled by USE_REAL_TIME_DATA
    in .env.
    """
    if not request.destination.strip():
        raise HTTPException(status_code=400, detail="Destination cannot be empty.")

    key = request.api_key or os.getenv("GEMINI_API_KEY")
    is_vertex = (
        os.getenv("VERTEX", "false").lower() in ("true", "1", "yes") or
        bool(os.getenv("GOOGLE_CLOUD_PROJECT")) or
        bool(os.getenv("GCP_PROJECT"))
    )
    if not key and not is_vertex:
        raise HTTPException(
            status_code=400,
            detail=(
                "Gemini API Key is missing. "
                "Please provide it in .env or via the settings panel."
            ),
        )

    try:
        orchestrator = HikePlanningOrchestrator(api_key=key)
        state = await orchestrator.run(
            destination=request.destination,
            lat=request.lat,
            lon=request.lon,
        )

        # Spread trail_facts at the top level so the frontend contract is unchanged,
        # then attach the supplementary orchestration fields underneath.
        trail_facts = state.trail_facts or {}
        return {
            **trail_facts,
            "weather_data": state.weather_data,
            "observations": state.observations,
            "errors": state.errors,
            "use_real_time_data": state.use_real_time_data,
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/generate-packing-list")
@app.post("/api/generate-packing")
async def generate_packing(request: GeneratePackingRequest):
    """Generate structured packing recommendations from trail facts and user answers."""
    key = request.api_key or os.getenv("GEMINI_API_KEY")
    is_vertex = (
        os.getenv("VERTEX", "false").lower() in ("true", "1", "yes") or
        bool(os.getenv("GOOGLE_CLOUD_PROJECT")) or
        bool(os.getenv("GCP_PROJECT"))
    )
    if not key and not is_vertex:
        raise HTTPException(
            status_code=400,
            detail=(
                "Gemini API Key is missing. "
                "Please provide it in .env or via the settings panel."
            ),
        )

    try:
        recommendations = await generate_packing_list(request.facts, request.answers, key)
        return recommendations
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

os.makedirs("frontend", exist_ok=True)
os.makedirs("frontend/css", exist_ok=True)
os.makedirs("frontend/js", exist_ok=True)

app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")
