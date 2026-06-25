"""
agent.py — Multi-agent orchestration framework for the Hike Planning assistant.

Architecture
------------
HikePlanningOrchestrator (orchestrator / root agent)
    ├── WeatherSubAgent     — isolated child; reads backend/skills/weather_agent/SKILL.md
    └── TrailFactsSubAgent  — isolated child; reads backend/skills/trail_agent/SKILL.md

Execution model
---------------
The orchestrator implements an explicit **Plan → Act → Observe** loop:

  Plan   Determine which sub-agents to dispatch based on the incoming request.
         Logic is driven by the rules in backend/skills/orchestrator.md:
           1. Receive destination string.
           2. Dispatch TrailFactsSubAgent to establish core trail data.
           3. Dispatch WeatherSubAgent with the resolved coordinates.
           4. Combine data and pass to generate_packing_list (packing_advisory.md).

  Act    Spawn each child agent inside its own Agent context with minimal tools
         and its own SKILL.md persona loaded via skills_paths.  The
         USE_REAL_TIME_DATA flag selects whether the child receives a live tool
         runner (the async provider wrapper) or calls a mock dict helper.

  Observe Collect, validate, and merge child outputs into the final
          HikePlanState that drives `generate_packing_list`.

SDK configuration
-----------------
• HikePlanningOrchestrator is backed by a LocalAgentConfig with enable_terminal_sandbox=True.

• Child agents:
  - Load their persona entirely from the SKILL.md discovered in their
    skills_paths directory.
  - Receive only the single custom tool they need.
  - Have enable_subagents=False to prevent unbounded delegation.

• USE_REAL_TIME_DATA (bool, from provider.py):
  - False → custom tool functions delegate to mock dict helpers (_mock_*).
  - True  → custom tool functions delegate to the async provider runners
            (_realtime_*) which call external / Antigravity-agent sources.
"""

import asyncio
import logging
import os
import datetime
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

import pydantic
from dotenv import load_dotenv
from google.antigravity import Agent, LocalAgentConfig, ToolContext, types
from google.antigravity.hooks import hooks, policy

# ---------------------------------------------------------------------------
# Shared provider imports (trail facts + weather + env flag)
# ---------------------------------------------------------------------------
from backend.provider import (
    USE_REAL_TIME_DATA,
    TrailFactsResponse,
    WeatherDataResponse,
    # Real-time async runners (used when USE_REAL_TIME_DATA=True)
    get_trail_facts,
    get_weather_data,
    # Mock dict helpers exposed for direct call when USE_REAL_TIME_DATA=False
    _mock_get_trail_facts,
    _mock_get_weather_data,
)

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

# ---------------------------------------------------------------------------
# Paths — each sub-agent's skills_paths entry points to its SKILL.md folder
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).parent.resolve()
_SKILLS_DIR = _BACKEND_DIR / "skills"

# Directories that each contain a SKILL.md (loaded via skills_paths)
_WEATHER_SKILL_DIR = _SKILLS_DIR / "weather_agent"   # → weather_agent/SKILL.md
_TRAIL_SKILL_DIR = _SKILLS_DIR / "trail_agent"       # → trail_agent/SKILL.md

# ---------------------------------------------------------------------------
# YAML Schema loading directly from native SKILL.md files
# ---------------------------------------------------------------------------
def _load_yaml_from_skill(skill_dir: Path) -> Dict[str, Any]:
    """Read the YAML frontmatter directly from the SKILL.md file inside skill_dir."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        logger.warning("[SKILL-LOAD] SKILL.md not found in %s", skill_dir)
        return {}
    try:
        content = skill_file.read_text(encoding="utf-8")
        parts = content.split("---")
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}
    except Exception as exc:
        logger.error("[SKILL-LOAD] Failed to parse YAML from SKILL.md in %s: %s", skill_dir, exc)
    return {}

# Programmatically load YAML structures
_TRAIL_SKILL_META = _load_yaml_from_skill(_TRAIL_SKILL_DIR)
_WEATHER_SKILL_META = _load_yaml_from_skill(_WEATHER_SKILL_DIR)

logger.info("[YAML-LOAD] Trail skill metadata parsed: %s", _TRAIL_SKILL_META)
logger.info("[YAML-LOAD] Weather skill metadata parsed: %s", _WEATHER_SKILL_META)


def _load_skill_md(skill_dir: Path) -> str:
    """
    Read the SKILL.md from *skill_dir* and return its body as plain text.
    """
    skill_file = skill_dir / "SKILL.md"
    try:
        return skill_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning(
            "[SKILL-LOAD] SKILL.md not found in %s — runtime context will be appended "
            "to an empty base.",
            skill_dir,
        )
        return ""


def _load_flat_skill(filename: str) -> str:
    """
    Read one of the flat .md skill files from backend/skills/ and return its
    content as a plain string for use in system_instructions.
    """
    path = _SKILLS_DIR / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning(
            "[SKILL-LOAD] Skill file not found: %s — falling back to empty string.", path
        )
        return ""


# ---------------------------------------------------------------------------
# Pydantic output schemas
# ---------------------------------------------------------------------------

class PackingItem(pydantic.BaseModel):
    name: str
    explanation: str
    confidence: str = "high"    # "high" | "low"
    source: str = "inferred"    # "fetched" | "user_provided" | "inferred"


class PackingRecommendations(pydantic.BaseModel):
    must_bring: List[PackingItem]
    recommended: List[PackingItem]
    optional: List[PackingItem]
    duration_hours: float
    duration_explanation: str
    water_liters: float
    water_explanation: str
    safety_inferences: List[str]
    confidence_level: str = "High"       # "High" | "Lower Confidence"
    weather_source: str = "fetched"      # "fetched" | "user_provided"
    trail_source: str = "fetched"        # "fetched" | "user_provided"


# ---------------------------------------------------------------------------
# Shared session state threaded through the Plan → Act → Observe loop
# ---------------------------------------------------------------------------

class HikePlanState(pydantic.BaseModel):
    """Mutable session state threaded through the Plan→Act→Observe loop."""

    destination: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    use_real_time_data: bool = False

    # Filled by sub-agents during Act phase
    trail_facts: Optional[Dict[str, Any]] = None
    weather_data: Optional[Dict[str, Any]] = None

    # Orchestration metadata
    plan_steps: List[str] = pydantic.Field(default_factory=list)
    observations: List[str] = pydantic.Field(default_factory=list)
    errors: List[str] = pydantic.Field(default_factory=list)
    token_usage: Dict[str, int] = pydantic.Field(default_factory=dict)
    started_at: str = pydantic.Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

@hooks.pre_turn
async def _pre_turn_log(data: str) -> types.HookResult:
    """Log every orchestrator turn before execution starts."""
    logger.info("[PRE-TURN] Orchestrator received prompt (%d chars)", len(data))
    return types.HookResult(allow=True)


@hooks.post_turn
async def _post_turn_log(data: str) -> None:
    """Log orchestrator turn completion."""
    logger.info("[POST-TURN] Orchestrator produced response (%d chars)", len(data))


@hooks.post_tool_call
async def _audit_tool_call(data: Any) -> None:
    """Audit log every tool call result for observability."""
    logger.info("[AUDIT] Tool call completed — result type: %s", type(data).__name__)


class _FallbackHook(hooks.OnToolErrorHook):
    """Intercept tool errors so the agent sees a descriptive recovery message
    rather than a raw Python traceback, preserving conversation continuity."""

    async def run(self, context: hooks.HookContext, data: Any) -> Optional[str]:
        logger.error("[TOOL-ERROR] %s: %s", type(data).__name__, data)
        if isinstance(data, pydantic.ValidationError):
            return (
                "[Tool input validation failed. Retry with corrected arguments: "
                + str(data)
                + "]"
            )
        if isinstance(data, (RuntimeError, ValueError)):
            return f"[Tool error: {data}. Please verify inputs and retry.]"
        return None  # Let the SDK handle anything else


# ---------------------------------------------------------------------------
# Sub-agent custom tool wrappers
# ---------------------------------------------------------------------------

async def _tool_get_trail_facts(destination: str, ctx: ToolContext) -> str:
    """Retrieve trail facts for a named hiking destination.

    Args:
        destination: Human-readable trail name or location query.
        ctx: ToolContext injected automatically; holds session variables.
    """
    use_rt = ctx.get_state("USE_REAL_TIME_DATA", str(USE_REAL_TIME_DATA)).lower() in {
        "true", "1", "yes"
    }
    logger.info(
        "[TOOL] get_trail_facts — destination=%r, USE_REAL_TIME_DATA=%s",
        destination,
        use_rt,
    )
    try:
        if use_rt:
            result: TrailFactsResponse = await get_trail_facts(destination)
        else:
            result = await _mock_get_trail_facts(destination)

        ctx.set_state("trail_facts_result", result.model_dump())
        return result.model_dump_json()
    except Exception as exc:
        logger.error("[TOOL] get_trail_facts failed: %s", exc)
        raise


async def _tool_get_weather_data(lat: float, lon: float, ctx: ToolContext) -> str:
    """Retrieve weather data for a geographic coordinate pair.

    Args:
        lat: Latitude in decimal degrees (−90 to 90).
        lon: Longitude in decimal degrees (−180 to 180).
        ctx: ToolContext injected automatically; holds session variables.
    """
    use_rt = ctx.get_state("USE_REAL_TIME_DATA", str(USE_REAL_TIME_DATA)).lower() in {
        "true", "1", "yes"
    }
    logger.info(
        "[TOOL] get_weather_data — lat=%s, lon=%s, USE_REAL_TIME_DATA=%s",
        lat,
        lon,
        use_rt,
    )
    try:
        if use_rt:
            result: WeatherDataResponse = await get_weather_data(lat, lon)
        else:
            result = _mock_get_weather_data(lat, lon)

        ctx.set_state("weather_data_result", result.model_dump())
        return result.model_dump_json()
    except Exception as exc:
        logger.error("[TOOL] get_weather_data failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# MCP Config Pool Selector
# ---------------------------------------------------------------------------
def _get_mcp_servers(use_real_time_data: bool) -> List[Any]:
    """Return the configured weather-service and brave-search MCP servers when enabled."""
    if not use_real_time_data:
        return []
    
    openweather_api_key = os.environ.get("OPENWEATHER_API_KEY")
    if not openweather_api_key:
        raise ValueError("OPENWEATHER_API_KEY environment variable is not set.")

    return [
        types.McpStdioServer(
            name="brave-search",
            command="/opt/homebrew/bin/npx",
            args=["-y", "@modelcontextprotocol/server-brave-search"]
        ),
        types.McpStdioServer(
            name="weather-service",
            command="/Users/olena/anaconda3/bin/mcp-server-weather",
            args=["--api_key", openweather_api_key]
        )
    ]


def _get_vertex_config() -> Dict[str, Any]:
    """Return Vertex AI configuration dict if Vertex is enabled in env."""
    is_vertex = (
        os.getenv("VERTEX", "false").lower() in ("true", "1", "yes") or
        bool(os.getenv("GOOGLE_CLOUD_PROJECT")) or
        bool(os.getenv("GCP_PROJECT"))
    )
    if is_vertex:
        return {
            "vertex": True,
            "project": os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT"),
            "location": os.getenv("VERTEX_LOCATION") or os.getenv("GOOGLE_CLOUD_REGION") or "us-central1"
        }
    return {}


# ---------------------------------------------------------------------------
# Child agent config constructors
# ---------------------------------------------------------------------------

def _build_trail_agent_config(api_key: str, use_real_time_data: bool) -> LocalAgentConfig:
    """Build an isolated LocalAgentConfig for the Trail Facts sub-agent."""
    runtime_context = (
        "---\n"
        "RUNTIME CONTEXT\n"
        f"USE_REAL_TIME_DATA: {str(use_real_time_data).lower()}\n"
        f"SKILL NAME: {_TRAIL_SKILL_META.get('name')}\n"
        f"SKILL DESCRIPTION: {_TRAIL_SKILL_META.get('description')}\n"
        "Your ONLY action is to call `get_trail_facts` with the destination provided "
        "and return the raw JSON result verbatim. Do NOT add commentary beyond the JSON."
    )
    v_config = _get_vertex_config()
    return LocalAgentConfig(
        api_key=api_key if api_key else None,
        enable_terminal_sandbox=True,
        skills_paths=[str(_TRAIL_SKILL_DIR)],
        system_instructions=runtime_context,
        tools=[_tool_get_trail_facts],
        capabilities=types.CapabilitiesConfig(
            enable_subagents=False,
            disabled_tools=[
                types.BuiltinTools.RUN_COMMAND,
                types.BuiltinTools.CREATE_FILE,
                types.BuiltinTools.EDIT_FILE,
                types.BuiltinTools.GENERATE_IMAGE,
                types.BuiltinTools.START_SUBAGENT,
            ],
        ),
        mcp_servers=_get_mcp_servers(use_real_time_data),
        policies=[policy.deny("run_command")],
        hooks=[_FallbackHook()],
        **v_config
    )


def _build_weather_agent_config(api_key: str, use_real_time_data: bool) -> LocalAgentConfig:
    """Build an isolated LocalAgentConfig for the Weather sub-agent."""
    runtime_context = (
        "---\n"
        "RUNTIME CONTEXT\n"
        f"USE_REAL_TIME_DATA: {str(use_real_time_data).lower()}\n"
        f"SKILL NAME: {_WEATHER_SKILL_META.get('name')}\n"
        f"SKILL DESCRIPTION: {_WEATHER_SKILL_META.get('description')}\n"
        "Your ONLY action is to call `get_weather_data` with the lat/lon provided "
        "and return the raw JSON result verbatim. Do NOT add commentary beyond the JSON."
    )
    v_config = _get_vertex_config()
    return LocalAgentConfig(
        api_key=api_key if api_key else None,
        enable_terminal_sandbox=True,
        skills_paths=[str(_WEATHER_SKILL_DIR)],
        system_instructions=runtime_context,
        tools=[_tool_get_weather_data],
        capabilities=types.CapabilitiesConfig(
            enable_subagents=False,
            disabled_tools=[
                types.BuiltinTools.RUN_COMMAND,
                types.BuiltinTools.CREATE_FILE,
                types.BuiltinTools.EDIT_FILE,
                types.BuiltinTools.GENERATE_IMAGE,
                types.BuiltinTools.START_SUBAGENT,
            ],
        ),
        mcp_servers=_get_mcp_servers(use_real_time_data),
        policies=[policy.deny("run_command")],
        hooks=[_FallbackHook()],
        **v_config
    )


def _build_orchestrator_config(api_key: str, use_real_time_data: bool) -> LocalAgentConfig:
    """Build the root LocalAgentConfig for the HikePlanningOrchestrator."""
    orchestrator_skill = _load_flat_skill("orchestrator.md")
    system_instructions = (
        f"{orchestrator_skill}\n\n"
        "---\n"
        "RUNTIME CONTEXT\n"
        f"USE_REAL_TIME_DATA: {str(use_real_time_data).lower()}\n"
        "You are the HikePlanningOrchestrator. Coordinate TrailFacts and Weather "
        "sub-agents sequentially as described in your skill, then synthesise the "
        "final packing plan. Never execute shell commands."
    )
    v_config = _get_vertex_config()
    return LocalAgentConfig(
        api_key=api_key if api_key else None,
        enable_terminal_sandbox=True,
        system_instructions=system_instructions,
        capabilities=types.CapabilitiesConfig(
            enable_subagents=True,
            disabled_tools=[
                types.BuiltinTools.RUN_COMMAND,
                types.BuiltinTools.CREATE_FILE,
                types.BuiltinTools.EDIT_FILE,
                types.BuiltinTools.GENERATE_IMAGE,
            ],
        ),
        mcp_servers=_get_mcp_servers(use_real_time_data),
        policies=[policy.deny("run_command")],
        hooks=[_FallbackHook()],
        **v_config
    )


# ---------------------------------------------------------------------------
# HikePlanningOrchestrator
# ---------------------------------------------------------------------------

class HikePlanningOrchestrator:
    """
    Root orchestrator that coordinates isolated child sub-agents for Weather
    and Trail Facts retrieval using the Plan → Act → Observe execution loop
    as specified in backend/skills/orchestrator.md.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key: str = api_key or os.environ.get("GEMINI_API_KEY", "")
        # Require key only if Vertex AI is not enabled in the environment
        is_vertex = (
            os.getenv("VERTEX", "false").lower() in ("true", "1", "yes") or
            bool(os.getenv("GOOGLE_CLOUD_PROJECT")) or
            bool(os.getenv("GCP_PROJECT"))
        )
        if not self.api_key and not is_vertex:
            raise RuntimeError(
                "No API key provided. Set GEMINI_API_KEY in your environment or "
                "pass api_key= to HikePlanningOrchestrator()."
            )

        self.use_real_time_data: bool = USE_REAL_TIME_DATA

        # Build the orchestrator's own LocalAgentConfig with sandbox enabled
        self._config: LocalAgentConfig = _build_orchestrator_config(self.api_key, self.use_real_time_data)

        logger.info(
            "[ORCHESTRATOR] Initialised — USE_REAL_TIME_DATA=%s, enable_terminal_sandbox=True",
            self.use_real_time_data,
        )

    # ------------------------------------------------------------------
    # Plan phase
    # ------------------------------------------------------------------

    def _plan(self, state: HikePlanState) -> List[str]:
        """Determine which sub-agents to dispatch based on user input coordinates."""
        steps: List[str] = ["fetch_trail_facts"]
        if state.lat is not None and state.lon is not None:
            steps.append("fetch_weather_data")
        state.plan_steps = steps
        logger.info("[PLAN] Dispatching steps: %s", steps)
        return steps

    # ------------------------------------------------------------------
    # Act phase — child sub-agents
    # ------------------------------------------------------------------

    async def _act_trail_facts(self, state: HikePlanState) -> None:
        """Spawn the Trail Facts sub-agent and capture its structured output."""
        # In mock mode, skip the LLM Agent entirely — call mock data directly
        # to avoid burning API quota and eliminate 429-induced delays.
        if not self.use_real_time_data:
            logger.info("[ACT] Mock mode — loading trail facts directly for %r", state.destination)
            try:
                mock_res = await _mock_get_trail_facts(state.destination)
                state.trail_facts = mock_res.model_dump()
                state.observations.append(
                    f"Trail facts loaded (mock): found={mock_res.found}, "
                    f"name={mock_res.name!r}, source={mock_res.source!r}"
                )
            except Exception as exc:
                logger.error("[ACT-ERROR] Mock trail load failed: %s", exc)
                state.errors.append(f"Mock trail load failed: {exc}")
                state.trail_facts = _blank_trail_facts(state.destination)
            return

        config = _build_trail_agent_config(self.api_key, self.use_real_time_data)

        prompt = (
            f"Retrieve trail facts for the destination: '{state.destination}'. "
            "Call get_trail_facts and return only the JSON result."
        )

        logger.info("[ACT] Starting Trail Facts sub-agent for %r", state.destination)
        try:
            async with Agent(config=config) as agent:
                response = await agent.chat(prompt)
                raw_text = await response.text()

                usage = agent.conversation.total_usage
                state.token_usage["trail_agent_prompt"] = usage.prompt_token_count
                state.token_usage["trail_agent_candidates"] = usage.candidates_token_count
                logger.info(
                    "[OBSERVE] Trail agent tokens — prompt=%d, candidates=%d",
                    usage.prompt_token_count,
                    usage.candidates_token_count,
                )

            parsed = _extract_json(raw_text)
            if parsed:
                state.trail_facts = parsed
                state.observations.append(
                    f"Trail facts retrieved: found={parsed.get('found')}, "
                    f"name={parsed.get('name')!r}, source={parsed.get('source')!r}"
                )
            else:
                raise ValueError(f"Trail agent returned unparseable text: {raw_text[:200]}")

        except Exception as exc:
            error_msg = f"Trail Facts sub-agent failed: {exc}"
            logger.error("[ACT-ERROR] %s", error_msg)
            state.errors.append(error_msg)
            state.trail_facts = _blank_trail_facts(state.destination)

    async def _act_weather_data(self, state: HikePlanState) -> None:
        """Spawn the Weather sub-agent and capture its structured output."""
        if state.lat is None or state.lon is None:
            logger.info("[ACT] Skipping Weather sub-agent — no coordinates.")
            return

        # In mock mode, skip the LLM Agent entirely — call mock data directly.
        if not self.use_real_time_data:
            logger.info(
                "[ACT] Mock mode — loading weather data directly for (%s, %s)",
                state.lat,
                state.lon,
            )
            try:
                mock_res = _mock_get_weather_data(state.lat, state.lon)
                state.weather_data = mock_res.model_dump()
                state.observations.append(
                    f"Weather data loaded (mock): status={mock_res.status!r}, "
                    f"condition={mock_res.condition!r}, temp={mock_res.temperature_f}°F"
                )
                if state.trail_facts and mock_res.status == "success":
                    _merge_weather_into_trail(state.trail_facts, state.weather_data)
            except Exception as exc:
                logger.error("[ACT-ERROR] Mock weather load failed: %s", exc)
                state.errors.append(f"Mock weather load failed: {exc}")
                state.weather_data = None
            return

        config = _build_weather_agent_config(self.api_key, self.use_real_time_data)

        prompt = (
            f"Retrieve weather data for coordinates lat={state.lat}, lon={state.lon}. "
            "Call get_weather_data and return only the JSON result."
        )

        logger.info(
            "[ACT] Starting Weather sub-agent for lat=%s, lon=%s",
            state.lat,
            state.lon,
        )
        try:
            async with Agent(config=config) as agent:
                response = await agent.chat(prompt)
                raw_text = await response.text()

                usage = agent.conversation.total_usage
                state.token_usage["weather_agent_prompt"] = usage.prompt_token_count
                state.token_usage["weather_agent_candidates"] = usage.candidates_token_count
                logger.info(
                    "[OBSERVE] Weather agent tokens — prompt=%d, candidates=%d",
                    usage.prompt_token_count,
                    usage.candidates_token_count,
                )

            parsed = _extract_json(raw_text)
            if parsed:
                state.weather_data = parsed
                state.observations.append(
                    f"Weather data retrieved: status={parsed.get('status')!r}, "
                    f"condition={parsed.get('condition')!r}, "
                    f"temp={parsed.get('temperature_f')}°F"
                )
                if state.trail_facts and parsed.get("status") == "success":
                    _merge_weather_into_trail(state.trail_facts, parsed)
            else:
                raise ValueError(f"Weather agent returned unparseable text: {raw_text[:200]}")

        except Exception as exc:
            error_msg = f"Weather sub-agent failed: {exc}"
            logger.error("[ACT-ERROR] %s", error_msg)
            state.errors.append(error_msg)
            state.weather_data = None

    # ------------------------------------------------------------------
    # Observe phase — consolidate and validate
    # ------------------------------------------------------------------

    def _observe(self, state: HikePlanState) -> None:
        """Validate the collected outputs and record the final session state."""
        if not state.trail_facts:
            state.trail_facts = _blank_trail_facts(state.destination)
            state.observations.append(
                "Observation: trail_facts was empty after Act phase; blank fallback applied."
            )

        total_tokens = sum(state.token_usage.values())
        state.observations.append(f"Total orchestration tokens used: {total_tokens}")
        logger.info("[OBSERVE] Orchestration complete — total tokens: %d", total_tokens)

        state.completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Public async runtime — Plan → Act → Observe
    # ------------------------------------------------------------------

    async def run(
        self,
        destination: str,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> HikePlanState:
        """Execute the full Plan → Act → Observe loop and return the populated HikePlanState."""
        if lat is None or lon is None:
            norm = destination.lower().strip()
            if "mission peak" in norm:
                lat, lon = 37.5, -121.9
            elif "henry cowell" in norm:
                lat, lon = 37.1, -122.0
            elif "eagle peak" in norm or "yosemite" in norm:
                lat, lon = 37.8, -119.6

        state = HikePlanState(
            destination=destination,
            lat=lat,
            lon=lon,
            use_real_time_data=self.use_real_time_data,
        )

        steps = self._plan(state)

        act_coroutines: List = []
        if "fetch_trail_facts" in steps:
            act_coroutines.append(self._act_trail_facts(state))
        if "fetch_weather_data" in steps:
            act_coroutines.append(self._act_weather_data(state))

        await asyncio.gather(*act_coroutines)

        self._observe(state)

        return state


# ---------------------------------------------------------------------------
# Deterministic hike parameter calculation
# ---------------------------------------------------------------------------

def calculate_hike_parameters(facts: Dict[str, Any], answers: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministically calculates hiking parameters following conservative safety rules."""
    distance = float(facts.get("distance_miles", 0.0))
    elevation = float(facts.get("elevation_gain_feet", 0.0))
    weather = facts.get("weather_forecast", "")
    terrain = facts.get("terrain", "")

    pace = answers.get("pace", "moderate")
    start_time_str = answers.get("start_time", "08:00 AM")
    footwear_pref = answers.get("footwear_preference", "sneakers")

    weather_source = facts.get("weather_source", "fetched")
    if facts.get("weather_status") in ["failed", "timeout"] or not facts.get("weather_forecast"):
        weather_source = "user_provided"

    trail_source = (
        "user_provided"
        if (
            facts.get("source") == "Manual Setup"
            or facts.get("trail_status") == "missing"
            or distance == 0
        )
        else "fetched"
    )

    weather_stale = False
    ts_str = facts.get("weather_timestamp")
    if weather_source == "fetched" and ts_str:
        try:
            ts = datetime.datetime.fromisoformat(ts_str)
            now = datetime.datetime.now(datetime.timezone.utc)
            if (now - ts).total_seconds() > 7200:
                weather_stale = True
        except Exception as exc:
            logger.debug("Staleness parse error: %s", exc)

    confidence_level = "High"
    if weather_source == "user_provided" or trail_source == "user_provided" or weather_stale:
        confidence_level = "Lower Confidence"

    if pace == "slow":
        speed, elev_penalty = 1.5, 1.0
    elif pace == "fast":
        speed, elev_penalty = 3.5, 0.3
    else:
        speed, elev_penalty = 2.5, 0.5

    duration = (distance / speed) + (elevation / 1000.0) * elev_penalty
    duration = max(round(duration, 1), 0.5)

    temp = 70.0
    temp_match = re.search(r"(\d+)\s*°F", weather)
    if temp_match:
        temp = float(temp_match.group(1))
    else:
        wl = weather.lower()
        if "hot" in wl:
            temp = 85.0
        elif "mild" in wl:
            temp = 70.0
        elif "cool" in wl:
            temp = 55.0

    water_rate = 0.5
    water_reasons = ["0.5L/hr baseline for moderate conditions"]
    if weather_source == "user_provided":
        water_reasons.append(f"Based on user-entered forecast: '{weather}'")
    if temp > 75:
        water_rate += 0.2
        water_reasons.append(f"+0.2L/hr for warm weather ({temp}°F)")
    if temp > 85:
        water_rate += 0.2
        water_reasons.append(f"+0.2L/hr additional for high heat ({temp}°F)")
    if elevation > 1500:
        water_rate += 0.1
        water_reasons.append("+0.1L/hr for moderate elevation gain (>1500ft)")
    if elevation > 3000:
        water_rate += 0.1
        water_reasons.append("+0.1L/hr additional for significant elevation gain (>3000ft)")

    total_water = round(duration * water_rate, 1)
    if total_water < 1.0:
        total_water = 1.0
        water_reasons.append("Adjusted to a minimum of 1.0L for safety")

    def _parse_time(t_str: str) -> int:
        t_str = t_str.upper().strip()
        m = re.match(r"(\d+):(\d+)\s*(AM|PM)?", t_str)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            mer = m.group(3)
            if mer == "PM" and h < 12:
                h += 12
            elif mer == "AM" and h == 12:
                h = 0
            return h * 60 + mi
        m2 = re.match(r"(\d+):(\d+)", t_str)
        if m2:
            return int(m2.group(1)) * 60 + int(m2.group(2))
        return 8 * 60

    start_min = _parse_time(start_time_str)
    end_min = start_min + int(duration * 60)
    sunset_str = facts.get("sunset", "8:00 PM")
    sunset_min = _parse_time(sunset_str)

    eh = (end_min // 60) % 24
    em = end_min % 60
    mer = "PM" if eh >= 12 else "AM"
    if eh > 12:
        eh -= 12
    elif eh == 0:
        eh = 12
    end_time_formatted = f"{eh}:{em:02d} {mer}"

    diff = sunset_min - end_min
    if diff <= 60:
        headlamp_status = "must_bring"
        headlamp_reason = (
            f"Your hike is estimated to finish at {end_time_formatted}, which is "
            f"very close to or after sunset ({sunset_str}). A headlamp is absolutely required."
        )
    elif diff <= 120:
        headlamp_status = "recommended"
        headlamp_reason = (
            f"Your hike is estimated to finish at {end_time_formatted}, about "
            f"{diff} minutes before sunset ({sunset_str}). A headlamp is recommended in case of delays."
        )
    else:
        headlamp_status = "optional"
        headlamp_reason = (
            f"Your hike is estimated to finish at {end_time_formatted}, well before "
            f"sunset ({sunset_str}). A headlamp is optional/precautionary."
        )

    charger_status = "optional"
    if duration > 8.0:
        charger_status = "must_bring"
    elif duration > 5.0:
        charger_status = "recommended"

    is_rough = (elevation > 1500) or any(
        w in terrain.lower()
        for w in ["steep", "rocky", "granite", "gravel", "switchback", "root"]
    )
    is_beginner_leaning = pace == "slow"
    # Poles: auto-recommended for difficult terrain or slow pace (beginner); optional otherwise
    if is_rough or is_beginner_leaning:
        poles_status = "recommended"
    else:
        poles_status = "optional"

    footwear_warning = ""
    if is_rough and footwear_pref in ["sandals", "sneakers"]:
        footwear_warning = (
            f"Terrain is steep/rocky, but preference is '{footwear_pref}'. "
            "Supportive trail runners or hiking boots are strongly advised."
        )

    safety_inferences: List[str] = []
    if headlamp_status == "must_bring":
        safety_inferences.append(
            "Late Return Risk: Hike extends near or past sunset. Headlamp is MUST BRING."
        )
    if footwear_warning:
        safety_inferences.append(f"Footwear Hazard: {footwear_warning}")
    if weather_stale:
        safety_inferences.append(
            "Stale Weather Warning: Weather data is older than 2 hours. Conditions may have changed."
        )
    if weather_source == "user_provided":
        safety_inferences.append(
            "User Estimated Weather: Packing suggestions rely on manual weather inputs and carry lower confidence."
        )
    if trail_source == "user_provided":
        safety_inferences.append(
            "User-Entered Trail Metrics: Calculations are based on manually entered distance/elevation."
        )

    return {
        "duration_hours": duration,
        "water_liters": total_water,
        "water_reasons": water_reasons,
        "water_rate": water_rate,
        "headlamp_status": headlamp_status,
        "headlamp_reason": headlamp_reason,
        "end_time_formatted": end_time_formatted,
        "charger_status": charger_status,
        "poles_status": poles_status,
        "footwear_warning": footwear_warning,
        "is_rough_terrain": is_rough,
        "temp": temp,
        "weather_source": weather_source,
        "trail_source": trail_source,
        "weather_stale": weather_stale,
        "confidence_level": confidence_level,
        "safety_inferences": safety_inferences,
    }


# ---------------------------------------------------------------------------
# Packing list generation
# ---------------------------------------------------------------------------

def compile_deterministic_packing_list(
    facts: Dict[str, Any],
    answers: Dict[str, Any],
) -> PackingRecommendations:
    """Deterministically compile hiking items following rules when Gemini is unreachable."""
    params = calculate_hike_parameters(facts, answers)
    
    must_bring = []
    recommended = []
    optional = []
    
    # 1. Water
    must_bring.append(PackingItem(
        name=f"{params['water_liters']} Liters of Water",
        explanation=f"Based on a consumption rate of {params['water_rate']}L/hr: {', '.join(params['water_reasons'])}.",
        confidence="low" if params['weather_source'] == "user_provided" or params['weather_stale'] else "high",
        source="inferred"
    ))
    
    # 2. Snacks
    must_bring.append(PackingItem(
        name="High-Energy Snacks",
        explanation=f"Pack nutrition (energy bars, nuts, dried fruit) for a {params['duration_hours']} hour hike.",
        confidence="high",
        source="inferred"
    ))
    
    # 3. First Aid Kit
    must_bring.append(PackingItem(
        name="Basic First Aid Kit",
        explanation="Always carry antiseptic wipes, bandages, and blister treatment for trail safety.",
        confidence="high",
        source="inferred"
    ))
    
    # 4. Navigation
    must_bring.append(PackingItem(
        name="Navigation (Map & Compass / GPS App)",
        explanation="Essential to stay on the route; trailhead is " + (facts.get("trailhead_location") or "not specified") + ".",
        confidence="high",
        source="fetched" if facts.get("found") else "user_provided"
    ))
    
    # Headlamp
    hl_item = PackingItem(
        name="Headlamp or Flashlight",
        explanation=params['headlamp_reason'],
        confidence="high",
        source="inferred"
    )
    if params['headlamp_status'] == "must_bring":
        must_bring.append(hl_item)
    elif params['headlamp_status'] == "recommended":
        recommended.append(hl_item)
    else:
        optional.append(hl_item)
        
    # Charger
    charger_item = PackingItem(
        name="Portable Phone Charger",
        explanation=f"Helps ensure phone battery doesn't die during a {params['duration_hours']} hour outing.",
        confidence="high",
        source="inferred"
    )
    if params['charger_status'] == "must_bring":
        must_bring.append(charger_item)
    elif params['charger_status'] == "recommended":
        recommended.append(charger_item)
    else:
        optional.append(charger_item)
        
    # Poles (auto-included based on terrain difficulty and pace; no user preference needed)
    poles_item = PackingItem(
        name="Trekking Poles",
        explanation=(
            "Recommended for steep/rocky terrain — provides extra stability and reduces knee impact."
            if params['poles_status'] == 'recommended'
            else "Optional extra stability aid for flatter trails."
        ),
        confidence="high",
        source="inferred"
    )
    if params['poles_status'] == "must_bring":
        must_bring.append(poles_item)
    elif params['poles_status'] == "recommended":
        recommended.append(poles_item)
    else:
        optional.append(poles_item)
        
    # Footwear warning / recommendation
    if params['footwear_warning']:
        recommended.append(PackingItem(
            name="Supportive Hiking Boots or Trail Runners",
            explanation=params['footwear_warning'],
            confidence="high",
            source="inferred"
        ))
    else:
        recommended.append(PackingItem(
            name=f"Hiking Footwear ({answers.get('footwear_preference', 'sneakers')})",
            explanation="Matches your preference for this trail terrain.",
            confidence="high",
            source="user_provided"
        ))
        
    # Standard layers
    recommended.append(PackingItem(
        name="Extra Clothing Layers",
        explanation="Bring a lightweight jacket or windbreaker in case weather conditions change.",
        confidence="high",
        source="inferred"
    ))
    recommended.append(PackingItem(
        name="Sun Protection (Sunscreen / Sunglasses / Hat)",
        explanation="Recommended to prevent sunburn and glare exposure.",
        confidence="high",
        source="inferred"
    ))
    
    # Optional items
    optional.append(PackingItem(
        name="Smartphone / Camera",
        explanation="Optional for photos and trail documentation.",
        confidence="high",
        source="user_provided"
    ))
    optional.append(PackingItem(
        name="Pocket Knife or Multi-tool",
        explanation="General utility and emergency gear item.",
        confidence="high",
        source="inferred"
    ))
    
    return PackingRecommendations(
        must_bring=must_bring,
        recommended=recommended,
        optional=optional,
        duration_hours=params['duration_hours'],
        duration_explanation=f"Estimated hiking duration: {params['duration_hours']} hours based on distance, elevation, and {answers.get('pace')} pace.",
        water_liters=params['water_liters'],
        water_explanation=f"Water target calculated as {params['water_liters']}L based on {params['duration_hours']} hours at {params['water_rate']}L/hr.",
        safety_inferences=params['safety_inferences'],
        confidence_level=params['confidence_level'],
        weather_source=params['weather_source'],
        trail_source=params['trail_source']
    )


async def generate_packing_list(
    facts: Dict[str, Any],
    answers: Dict[str, Any],
    api_key: str,
) -> Dict[str, Any]:
    """Generate structured packing recommendations from trail facts and user answers."""
    params = calculate_hike_parameters(facts, answers)

    # Load strategy docs
    orchestrator_skill = _load_flat_skill("orchestrator.md")
    packing_skill = _load_flat_skill("packing_advisory.md")

    system_instructions = (
        f"{orchestrator_skill}\n\n"
        "---\n"
        f"{packing_skill}\n\n"
        "---\n"
        "STRUCTURED OUTPUT RULES\n"
        "Group items strictly into must_bring, recommended, and optional lists:\n"
        "- must_bring: Essential for safety and basic survival on this specific hike.\n"
        "- recommended: Highly useful for comfort, efficiency, and prevention of issues.\n"
        "- optional: Precautionary or luxury items.\n\n"
        "For EACH item specify 'source' and 'confidence':\n"
        "- source: 'fetched' | 'user_provided' | 'inferred'\n"
        "- confidence: 'low' if derived from stale/user-estimated data, otherwise 'high'\n\n"
        "Use the pre-calculated parameters in the prompt. Explain the logic transparently."
    )

    v_config = _get_vertex_config()
    config = LocalAgentConfig(
        api_key=api_key if api_key else None,
        enable_terminal_sandbox=True,
        system_instructions=system_instructions,
        response_schema=PackingRecommendations,
        capabilities=types.CapabilitiesConfig(
            enable_subagents=False,
            disabled_tools=[
                types.BuiltinTools.RUN_COMMAND,
                types.BuiltinTools.CREATE_FILE,
                types.BuiltinTools.EDIT_FILE,
                types.BuiltinTools.GENERATE_IMAGE,
                types.BuiltinTools.START_SUBAGENT,
            ],
        ),
        mcp_servers=_get_mcp_servers(USE_REAL_TIME_DATA),
        policies=[policy.deny("run_command")],
        hooks=[_FallbackHook()],
        **v_config
    )

    prompt = f"""
    --- HIKING FACTS ---
    Trail Name: {facts.get('name')}
    Distance: {facts.get('distance_miles')} miles
    Elevation Gain: {facts.get('elevation_gain_feet')} feet
    Trailhead: {facts.get('trailhead_location')}
    Weather: {facts.get('weather_forecast')}
    Sunrise: {facts.get('sunrise')} | Sunset: {facts.get('sunset')}
    Terrain: {facts.get('terrain')}
    Weather Status: {facts.get('weather_status')} | Weather Timestamp: {facts.get('weather_timestamp')}

    --- USER PROVIDED ANSWERS ---
    Pace: {answers.get('pace')}
    Start Time: {answers.get('start_time')}
    Has Poles: Auto (terrain-based)
    Footwear Preference: {answers.get('footwear_preference')}

    --- PRE-CALCULATED INFERENCES (ADVICE) ---
    Estimated Duration: {params['duration_hours']} hours
    Estimated End Time: {params['end_time_formatted']} (Sunset: {facts.get('sunset')})
    Calculated Water: {params['water_liters']} Liters (Rate: {params['water_rate']}L/hr. Reasons: {", ".join(params['water_reasons'])})
    Headlamp Need: {params['headlamp_status'].upper()} - {params['headlamp_reason']}
    Portable Charger Need: {params['charger_status'].upper()} (Hike duration is {params['duration_hours']} hours)
    Hiking Poles Recommendation: {params['poles_status'].upper()}
    Footwear Warning: {params['footwear_warning'] if params['footwear_warning'] else 'None'}

    --- SOURCE & CONFIDENCE METADATA ---
    Weather Source: {params['weather_source']} (Is stale: {params['weather_stale']})
    Trail Source: {params['trail_source']}
    Overall Confidence: {params['confidence_level']}
    Safety Inferences: {params['safety_inferences']}

    Please generate the PackingRecommendations JSON. Make sure to:
    1. Include the pre-calculated water recommendation under 'must_bring' (e.g., '{params['water_liters']} Liters of Water') with the exact explanation. Set its source='inferred', and confidence='low' if weather is stale or user-provided.
    2. Add headlamp, portable charger, and poles matching the pre-calculated needs. Tag their source='inferred' and confidence appropriately.
    3. Include other standard items (snacks, layers, sunscreen, first aid kit, map). Set their sources (e.g., map/trailhead is source='fetched' if trail facts are fetched, footwear is source='user_provided').
    4. Fill in the schema's metadata fields: confidence_level, weather_source, and trail_source matching the metadata above.
    """

    try:
        async with Agent(config=config) as agent:
            response = await agent.chat(prompt)
            structured_data = await response.structured_output()
            if not structured_data:
                raise ValueError(
                    "Failed to generate structured packing recommendations from the agent."
                )
            return structured_data
    except Exception as exc:
        logger.error("[LLM-ERROR] generate_packing_list failed: %s. Falling back to deterministic compilation.", exc)
        return compile_deterministic_packing_list(facts, answers)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from *text*, returning None if not found."""
    import json

    text = re.sub(r"```(?:json)?\s*", "", text).strip()

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _blank_trail_facts(destination: str) -> Dict[str, Any]:
    """Return a safe-default trail facts dict for an unknown destination."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "name": destination,
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
        "weather_status": "failed",
        "weather_timestamp": (now - datetime.timedelta(minutes=5)).isoformat(),
        "weather_error": "Data unavailable — manual setup required.",
    }


def _merge_weather_into_trail(
    trail: Dict[str, Any],
    weather: Dict[str, Any],
) -> None:
    """Enrich *trail* dict in-place with higher-fidelity weather fields."""
    if weather.get("status") != "success":
        return

    if not trail.get("weather_forecast"):
        trail["weather_forecast"] = (
            f"{weather.get('condition', 'Unknown')}, "
            f"{weather.get('temperature_f', '?')}°F, "
            f"wind {weather.get('wind_mph', '?')} mph"
        )

    if trail.get("sunrise") in ("", "6:00 AM"):
        trail["sunrise"] = weather.get("sunrise", trail.get("sunrise", "6:00 AM"))
    if trail.get("sunset") in ("", "8:00 PM"):
        trail["sunset"] = weather.get("sunset", trail.get("sunset", "8:00 PM"))
