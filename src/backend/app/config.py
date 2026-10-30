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

INCIDENT_ID = os.environ.get("INCIDENT_ID", "INC-2026-1012-01")
INCIDENT_TITLE = os.environ.get("INCIDENT_TITLE", "")
INCIDENT_LOCATION = os.environ.get("INCIDENT_LOCATION", "")
# t_rel_s = 0 for every module. The default matches the demo scenario (scripts/scenario.py::T0); for a
# real case set it, or clock times in reports are placeholders (REFERENCE_TIME_CONFIRMED is False).
INCIDENT_REFERENCE_TIME = datetime.fromisoformat(
    os.environ.get("INCIDENT_REFERENCE_TIME", "2026-10-12T17:42:00+05:30"))
REFERENCE_TIME_CONFIRMED = "INCIDENT_REFERENCE_TIME" in os.environ


def incident_file() -> Path:
    return STORAGE_DIR / "incident.json"


def apply_incident(data: dict) -> None:
    """Make saved incident details the live settings (module globals read at call time)."""
    global INCIDENT_ID, INCIDENT_TITLE, INCIDENT_LOCATION, INCIDENT_REFERENCE_TIME, REFERENCE_TIME_CONFIRMED
    INCIDENT_ID = data.get("incident_id") or INCIDENT_ID
    INCIDENT_TITLE = data.get("title", INCIDENT_TITLE) or ""
    INCIDENT_LOCATION = data.get("location", INCIDENT_LOCATION) or ""
    if data.get("reference_time"):
        INCIDENT_REFERENCE_TIME = datetime.fromisoformat(str(data["reference_time"]))
        REFERENCE_TIME_CONFIRMED = True


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
