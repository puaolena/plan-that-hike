import pytest
from unittest.mock import patch, AsyncMock
from backend.agent import (
    calculate_hike_parameters,
    compile_deterministic_packing_list,
    HikePlanningOrchestrator,
    HikePlanState,
    _extract_json,
    _blank_trail_facts,
    _merge_weather_into_trail,
)

# 1. Test calculation rules in calculate_hike_parameters
def test_calculate_hike_parameters_basic():
    # Moderate pace, flat terrain, mild weather, plenty of daylight
    facts = {
        "name": "Flat Trail",
        "distance_miles": 5.0,
        "elevation_gain_feet": 100.0,
        "weather_forecast": "Sunny, 70°F",
        "sunset": "08:00 PM",
        "terrain": "Flat dirt path",
        "weather_source": "fetched",
        "source": "Mock Database",
        "found": True,
    }
    answers = {
        "pace": "moderate",
        "start_time": "08:00 AM",
        "footwear_preference": "hiking boots",
    }
    
    params = calculate_hike_parameters(facts, answers)
    
    # pace=moderate (2.5 mph) -> 5.0 / 2.5 = 2.0 hours. Elev = 100 -> negligible.
    assert params["duration_hours"] == 2.0
    
    # 2.0 hours * 0.5L/hr = 1.0L water
    assert params["water_liters"] == 1.0
    assert params["water_rate"] == 0.5
    
    # 8:00 AM + 2 hours = 10:00 AM (well before 8:00 PM sunset)
    assert params["headlamp_status"] == "optional"
    
    # duration = 2.0 <= 5.0 -> optional charger
    assert params["charger_status"] == "optional"
    
    # Flat terrain + moderate pace -> optional poles
    assert params["poles_status"] == "optional"
    assert not params["footwear_warning"]

def test_calculate_hike_parameters_adjustments():
    # Test pace effects
    facts = {
        "name": "Standard Trail",
        "distance_miles": 6.0,
        "elevation_gain_feet": 1000.0,
        "weather_forecast": "Mild, 70°F",
        "sunset": "08:00 PM",
        "terrain": "Dirt",
        "found": True,
    }
    
    # Slow pace (1.5 mph, 1.0 hr per 1000ft elev penalty)
    answers_slow = {"pace": "slow", "start_time": "08:00 AM"}
    params_slow = calculate_hike_parameters(facts, answers_slow)
    # (6.0 / 1.5) + (1000 / 1000) * 1.0 = 4.0 + 1.0 = 5.0 hours
    assert params_slow["duration_hours"] == 5.0
    
    # Fast pace (3.5 mph, 0.3 hr per 1000ft elev penalty)
    answers_fast = {"pace": "fast", "start_time": "08:00 AM"}
    params_fast = calculate_hike_parameters(facts, answers_fast)
    # (6.0 / 3.5) + (1000 / 1000) * 0.3 = 1.71 + 0.3 = 2.01 -> ~2.0 hours
    assert params_fast["duration_hours"] == 2.0

def test_calculate_hike_parameters_heat_and_elevation_water():
    # Test heat and elevation additions to water calculation
    facts = {
        "name": "Hot Mountain",
        "distance_miles": 5.0,
        "elevation_gain_feet": 3200.0,
        "weather_forecast": "Very hot, 90°F",
        "sunset": "08:00 PM",
        "terrain": "Rocky",
        "found": True,
    }
    answers = {
        "pace": "moderate",
        "start_time": "08:00 AM",
    }
    
    params = calculate_hike_parameters(facts, answers)
    
    # duration = (5.0 / 2.5) + (3200 / 1000) * 0.5 = 2.0 + 1.6 = 3.6 hours
    assert params["duration_hours"] == 3.6
    
    # water_rate:
    # 0.5 baseline
    # + 0.2 (temp > 75)
    # + 0.2 (temp > 85)
    # + 0.1 (elevation > 1500)
    # + 0.1 (elevation > 3000)
    # Total rate = 1.1L/hr
    assert params["water_rate"] == pytest.approx(1.1)
    # total water = 3.6 * 1.1 = 3.96 -> round(3.96, 1) = 4.0L
    assert params["water_liters"] == 4.0

def test_calculate_hike_parameters_headlamp_and_safety():
    # Hike finishing near/after sunset
    facts = {
        "name": "Sunset Peak",
        "distance_miles": 10.0,
        "elevation_gain_feet": 2000.0,
        "weather_forecast": "Sunny, 70°F",
        "sunset": "06:00 PM",
        "terrain": "Steep rocky trail",
        "found": True,
    }
    answers = {
        "pace": "slow",
        "start_time": "01:00 PM",
        "footwear_preference": "sneakers",
    }
    
    # duration = (10 / 1.5) + (2000 / 1000) * 1.0 = 6.67 + 2.0 = 8.67 -> ~8.7 hours
    # start 01:00 PM (13:00) -> end at ~09:42 PM (which is after 06:00 PM sunset)
    params = calculate_hike_parameters(facts, answers)
    
    assert params["headlamp_status"] == "must_bring"
    assert "sunset" in params["headlamp_reason"]
    
    # duration > 8.0 hours -> charger must_bring
    assert params["charger_status"] == "must_bring"
    
    # steep rocky terrain -> footwear warning since pref is sneakers
    assert "Supportive trail runners or hiking boots are strongly advised" in params["footwear_warning"]
    
    # Trekking poles recommended due to steep/rocky terrain
    assert params["poles_status"] == "recommended"
    
    # Verify safety inferences contain sunset warning and footwear hazard
    assert any("Late Return Risk" in inf for inf in params["safety_inferences"])
    assert any("Footwear Hazard" in inf for inf in params["safety_inferences"])

# 2. Test compile_deterministic_packing_list
def test_compile_deterministic_packing_list():
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
    
    recs = compile_deterministic_packing_list(facts, answers)
    
    # Verify correct structure
    assert recs.water_liters >= 1.0
    assert len(recs.must_bring) >= 2
    assert len(recs.recommended) >= 2
    assert len(recs.optional) >= 1
    
    # Check that it includes water, snacks, map
    must_bring_names = [item.name for item in recs.must_bring]
    assert any("Water" in name for name in must_bring_names)
    assert any("Snacks" in name for name in must_bring_names)
    assert any("Navigation" in name for name in must_bring_names)

# 3. Test HikePlanningOrchestrator run in mock mode
@pytest.mark.asyncio
async def test_orchestrator_mock_mode():
    with patch("backend.agent.USE_REAL_TIME_DATA", False):
        orch = HikePlanningOrchestrator(api_key="fake-key")
        assert orch.use_real_time_data is False
        
        # Test the plan phase
        state = HikePlanState(destination="Mission Peak", lat=37.5, lon=-121.9)
        steps = orch._plan(state)
        assert "fetch_trail_facts" in steps
        assert "fetch_weather_data" in steps
        
        # Test full mock run
        res_state = await orch.run(destination="Mission Peak", lat=37.5, lon=-121.9)
        assert res_state.trail_facts is not None
        assert res_state.weather_data is not None
        assert res_state.trail_facts["found"] is True
        assert "Mission Peak" in res_state.trail_facts["name"]
        assert "Sunny" in res_state.trail_facts["weather_forecast"]

# 4. Helper function tests
def test_extract_json():
    # Valid json block
    text = "Here is the response: ```json\n{\n  \"name\": \"Yosemite\"\n}\n``` hope this helps"
    json_data = _extract_json(text)
    assert json_data == {"name": "Yosemite"}
    
    # Text without code block
    text_raw = "some random stuff {\"foo\": \"bar\"} trailing text"
    json_data_raw = _extract_json(text_raw)
    assert json_data_raw == {"foo": "bar"}
    
    # Invalid json
    text_invalid = "not json at all"
    assert _extract_json(text_invalid) is None

def test_blank_trail_facts():
    facts = _blank_trail_facts("Arbitrary Trail")
    assert facts["name"] == "Arbitrary Trail"
    assert facts["distance_miles"] == 0.0
    assert facts["found"] is False
    assert facts["weather_status"] == "failed"

def test_merge_weather_into_trail():
    trail = {
        "name": "Custom",
        "weather_forecast": "",
        "sunrise": "6:00 AM",
        "sunset": "8:00 PM",
    }
    weather = {
        "status": "success",
        "condition": "Cloudy",
        "temperature_f": 58.0,
        "wind_mph": 12.0,
        "sunrise": "6:15 AM",
        "sunset": "8:10 PM",
    }
    
    _merge_weather_into_trail(trail, weather)
    
    assert "Cloudy, 58.0°F, wind 12.0 mph" in trail["weather_forecast"]
    assert trail["sunrise"] == "6:15 AM"
    assert trail["sunset"] == "8:10 PM"
