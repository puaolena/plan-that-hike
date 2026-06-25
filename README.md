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

- Python 3.10+
- A Gemini API Key (obtain from [Google AI Studio](https://aistudio.google.com/app/api-keys))

## Setup & Running Locally

1. Install the dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```

2. Add your Gemini API Key. You can either:
   - Create a `.env` file in the root directory:
     ```env
     GEMINI_API_KEY=your_actual_api_key_here
     ```
   - Or paste it into the Settings Modal (top right gear icon) directly in the web browser.

3. Start the server:
   ```bash
   uvicorn backend.main:app --reload
   ```

4. Open [http://localhost:8000](http://localhost:8000) in your web browser.
