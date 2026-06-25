import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from backend.main import app
from backend.agent import compile_deterministic_packing_list

client = TestClient(app)

def test_check_api_key_endpoint():
    with patch("os.getenv", return_value="fake-api-key"):
        response = client.get("/api/check-key")
        assert response.status_code == 200
        assert response.json() == {"has_key": True}
        
    with patch("os.getenv", return_value=""):
        response = client.get("/api/check-key")
        assert response.status_code == 200
        assert response.json() == {"has_key": False}

def test_fetch_trail_endpoint_success():
    # Use mock data mode (default)
    with patch("backend.agent.USE_REAL_TIME_DATA", False), \
         patch("backend.main.os.getenv", return_value="fake-api-key"):
         
        # Test /api/fetch-trail-data (or /api/fetch-trail)
        payload = {
            "destination": "Mission Peak",
            "lat": 37.5,
            "lon": -121.9
        }
        response = client.post("/api/fetch-trail-data", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert "Mission Peak" in data["name"]
        assert data["found"] is True
        assert data["weather_data"] is not None
        assert data["use_real_time_data"] is False
        assert len(data["observations"]) > 0

def test_fetch_trail_endpoint_validation():
    # Destination cannot be empty
    payload = {
        "destination": "   ",
        "api_key": "fake-key"
    }
    response = client.post("/api/fetch-trail-data", json=payload)
    assert response.status_code == 400
    assert "Destination cannot be empty" in response.json()["detail"]

    # Missing API key (when not present in env)
    payload_no_key = {
        "destination": "Mission Peak"
    }
    with patch("backend.main.os.getenv", return_value=""):
        response = client.post("/api/fetch-trail-data", json=payload_no_key)
        assert response.status_code == 400
        assert "Gemini API Key is missing" in response.json()["detail"]

@pytest.mark.asyncio
async def test_generate_packing_endpoint():
    facts = {
        "name": "Henry Cowell",
        "distance_miles": 3.5,
        "elevation_gain_feet": 200.0,
        "weather_forecast": "Cool, 62°F",
        "sunset": "08:30 PM",
        "terrain": "Flat soft dirt",
        "found": True,
    }
    answers = {
        "pace": "moderate",
        "start_time": "09:00 AM",
        "footwear_preference": "sneakers",
    }
    payload = {
        "facts": facts,
        "answers": answers,
        "api_key": "fake-key"
    }

    # Patch generate_packing_list to avoid calling Gemini API, return deterministic result
    deterministic_res = compile_deterministic_packing_list(facts, answers)
    
    with patch("backend.main.generate_packing_list", return_value=deterministic_res.model_dump()):
        response = client.post("/api/generate-packing-list", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert "must_bring" in data
        assert "recommended" in data
        assert "optional" in data
        assert data["duration_hours"] == 1.5 # 3.5 / 2.5 + 200/1000 * 0.5 = 1.4 + 0.1 = 1.5 hours
        assert data["water_liters"] == 1.0 # minimum of 1.0L

def test_static_and_root_endpoints():
    # Root should return index.html (which is read from frontend/index.html)
    with patch("backend.main.FileResponse") as mock_response:
        mock_response.return_value = "index_html_content"
        response = client.get("/")
        assert response.status_code == 200
