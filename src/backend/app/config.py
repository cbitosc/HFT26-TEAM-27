"""Paths and settings. Override with environment variables where noted."""
import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = REPO_ROOT / "fixtures"
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", REPO_ROOT / "storage"))
UPLOAD_DIR = STORAGE_DIR / "uploads"
RUN_DIR = STORAGE_DIR / "runs" / "latest"

INCIDENT_ID = os.environ.get("INCIDENT_ID", "INC-2026-1012-01")
# t_rel_s = 0 for every module. Matches scripts/scenario.py::T0.
INCIDENT_REFERENCE_TIME = datetime.fromisoformat(
    os.environ.get("INCIDENT_REFERENCE_TIME", "2026-10-12T17:42:00+05:30"))

# Featherless (OpenAI-compatible) — used by modules 1, 2 and optionally 4.
FEATHERLESS_API_KEY = os.environ.get("FEATHERLESS_API_KEY", "")
FEATHERLESS_BASE_URL = os.environ.get("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")
FEATHERLESS_WITNESS_MODEL = os.environ.get("FEATHERLESS_WITNESS_MODEL", "Qwen/Qwen2.5-7B-Instruct")

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "video": {".mp4", ".mov", ".avi", ".mkv"},
    "witness": {".txt"},
    "sensor": {".csv"},
}
