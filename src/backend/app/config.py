"""Paths and settings. Override with environment variables where noted.

Incident details (id, title, location, reference time) can also be set per case through
PUT /api/incident; they are saved in storage/incident.json and applied here (apply_incident).
"""
import json
import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = REPO_ROOT / "fixtures"
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", REPO_ROOT / "storage"))
UPLOAD_DIR = STORAGE_DIR / "uploads"
RUN_DIR = STORAGE_DIR / "runs" / "latest"

INCIDENT_ID = os.environ.get("INCIDENT_ID", "CASE-UNNAMED")
INCIDENT_TITLE = os.environ.get("INCIDENT_TITLE", "")
INCIDENT_LOCATION = os.environ.get("INCIDENT_LOCATION", "")
# t_rel_s = 0 for every module. The default matches the demo scenario (scripts/scenario.py::T0); for a
# real case set it, or clock times in reports are placeholders (REFERENCE_TIME_CONFIRMED is False).
INCIDENT_REFERENCE_TIME = datetime.fromisoformat(
    os.environ.get("INCIDENT_REFERENCE_TIME", "2026-10-12T17:42:00+05:30"))
REFERENCE_TIME_CONFIRMED = "INCIDENT_REFERENCE_TIME" in os.environ
# Where t = 0 came from: "case" (set by the investigator), "evidence" (earliest timestamp in the sensor
# files, set per run) or "default" (a placeholder; clock times must not be shown).
REFERENCE_SOURCE = "case" if REFERENCE_TIME_CONFIRMED else "default"
REFERENCE_DETAIL = ""

# What the investigator knows about the case. Empty = no assumptions (no junction, no vehicle names,
# no lane-marking standard). The demo scenario declares its own in data/case.json.
CASE_VEHICLES: list[dict] = []
GPS_ORIGIN: tuple[float, float] | None = None  # set per run: one origin for every GPS file
CASE_SCENE: dict = {"type": "unknown"}
LANE_MARKINGS: dict | None = None

# Lane-line standards for automatic camera calibration. Only well-established ones; verify for your road
# and pass a custom {dash_m, gap_m, lane_width_m} otherwise.
LANE_PRESETS: dict[str, dict] = {
    "de_motorway": {"dash_m": 6.0, "gap_m": 12.0, "lane_width_m": 3.75},   # Leitlinie on Autobahn
    "de_urban": {"dash_m": 3.0, "gap_m": 6.0, "lane_width_m": 3.25},       # Leitlinie inside towns
    "us_freeway": {"dash_m": 3.048, "gap_m": 9.144, "lane_width_m": 3.66},  # 10 ft line, 30 ft gap, 12 ft lane
}

def incident_file() -> Path:
    return STORAGE_DIR / "incident.json"


def apply_incident(data: dict) -> None:
    """Make saved incident details the live settings (module globals read at call time)."""
    global INCIDENT_ID, INCIDENT_TITLE, INCIDENT_LOCATION, INCIDENT_REFERENCE_TIME, REFERENCE_TIME_CONFIRMED
    global CASE_VEHICLES, CASE_SCENE, LANE_MARKINGS, REFERENCE_SOURCE
    INCIDENT_ID = data.get("incident_id") or INCIDENT_ID
    INCIDENT_TITLE = data.get("title", INCIDENT_TITLE) or ""
    INCIDENT_LOCATION = data.get("location", INCIDENT_LOCATION) or ""
    if data.get("reference_time"):
        INCIDENT_REFERENCE_TIME = datetime.fromisoformat(str(data["reference_time"]))
        REFERENCE_TIME_CONFIRMED = True
        REFERENCE_SOURCE = "case"
    elif "reference_time" in data:  # an explicit null clears it: t = 0 comes from the evidence again
        REFERENCE_TIME_CONFIRMED = False
        REFERENCE_SOURCE = "default"
    if "vehicles" in data:
        CASE_VEHICLES = list(data["vehicles"] or [])
    if "scene" in data:
        CASE_SCENE = dict(data["scene"] or {"type": "unknown"})
    if "lane_markings" in data:
        lm = data["lane_markings"]
        if isinstance(lm, str):
            if lm not in LANE_PRESETS:
                raise ValueError(f"unknown lane-marking preset '{lm}'; one of {', '.join(LANE_PRESETS)}")
            lm = {"preset": lm, **LANE_PRESETS[lm]}
        LANE_MARKINGS = dict(lm) if lm else None


def case_snapshot() -> dict:
    """Everything apply_incident accepts, as currently set (for saving and for the API)."""
    return {"incident_id": INCIDENT_ID, "title": INCIDENT_TITLE, "location": INCIDENT_LOCATION,
            **({"reference_time": INCIDENT_REFERENCE_TIME.isoformat()} if REFERENCE_TIME_CONFIRMED else {}),
            "vehicles": CASE_VEHICLES, "scene": CASE_SCENE, "lane_markings": LANE_MARKINGS}


if incident_file().exists():
    try:
        apply_incident(json.loads(incident_file().read_text()))
    except (ValueError, OSError):
        pass

# Featherless (OpenAI-compatible) — used by modules 1, 2 and optionally 4.
FEATHERLESS_API_KEY = os.environ.get("FEATHERLESS_API_KEY", "")
FEATHERLESS_BASE_URL = os.environ.get("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")
# Qwen3-30B-A3B-2507 recovered 8/8 key claims of the demo statements (Qwen2.5-7B: 6/8) at 1 concurrency unit.
FEATHERLESS_WITNESS_MODEL = os.environ.get("FEATHERLESS_WITNESS_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507")

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "video": {".mp4", ".mov", ".avi", ".mkv"},
    "witness": {".txt"},
    "sensor": {".csv"},
}
