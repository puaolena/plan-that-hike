# Pack for This Hike

A mobile-friendly, high-fidelity web application to generate personalized packing recommendations for day hikes. 

Built with the **Google Antigravity SDK**, **FastAPI**, and modern **Vanilla HTML/CSS/JS**.

## Features

1. **Trail Facts Retrieval**: Fetch hike details (distance, elevation, trailhead, weather, sunrise/sunset, terrain).
   - Mock data for **Mission Peak**, **Henry Cowell Redwood State Park**, and **Yosemite Eagle Peak**.
   - AI Fallback using the Antigravity Agent to fetch details for arbitrary trails.
2. **Interactive review**: Allows users to review and edit facts/conditions before generating lists.
3. **Personalization Questions**: Tailor packing based on planned day, start time, pace, hiking poles ownership, and footwear preference.
4. **Conservative & Transparent recommendation logic**:
   - Baseline water: `0.5L / hour`, adjusted for heat and elevation.
   - Smart headlamp suggestion if estimated return is near/after sunset.
   - Smart portable charger suggestion for long hikes.
   - Hiking poles & footwear warnings based on terrain steepness.
5. **Interactive Packing Checklist**: Users can cross off items as they pack in the browser.
6. **Sources Transparency**: Clearly highlights what information came from fetched facts, user preferences, or inferred safety logic.

## Requirements

- **Python 3.10+**
- **Gemini API Key**: Required for AI-based fallback trail fact retrieval and packing recommendations. (Obtain from [Google AI Studio](https://aistudio.google.com/app/api-keys))
- **Real-Time Data Mode Keys** (Optional, required only to disable mock mode and use live external data sources):
  - **OpenWeather API Key**: Used by the weather-service MCP to fetch real-time weather forecasts. (Obtain from [OpenWeather](https://openweathermap.org/api))
  - **Brave Search API Key**: Used by the brave-search MCP to perform web queries for arbitrary trail facts. (Obtain from [Brave Search API](https://brave.com/search/api/))

## Setup & Running Locally

1. Install the dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```

2. Configure your environment variables. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   Open the `.env` file and configure the keys:
   - Paste your `GEMINI_API_KEY` (or enter it directly via the in-app Settings gear icon).
   - Set `USE_REAL_TIME_DATA=true` to enable live data retrieval.
   - If `USE_REAL_TIME_DATA` is `true`, configure your `OPENWEATHER_API_KEY` and `BRAVE_API_KEY` as well.

3. Start the server:
   ```bash
   uvicorn backend.main:app --reload
   ```

4. Open [http://localhost:8000](http://localhost:8000) in your web browser.
