"""Export the Pydantic contract (src/backend/app/schemas.py) as JSON Schema
into docs/contracts/schemas/, for consumers that don't read Python.

Usage: python scripts/export_schemas.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "backend"))

from app.schemas import AnalysisJob, AnalysisRun, AuditEvent, IncidentReport, ConflictReport, Evidence, ObservationSet, Reconstruction, Timeline  # noqa: E402

OUT = ROOT / "docs" / "contracts" / "schemas"
MODELS = {
    "evidence": Evidence,
    "observation_set": ObservationSet,
    "timeline": Timeline,
    "conflict_report": ConflictReport,
    "reconstruction": Reconstruction,
    "analysis_run": AnalysisRun,
    "analysis_job": AnalysisJob,
    "incident_report": IncidentReport,
    "audit_event": AuditEvent,
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, model in MODELS.items():
        (OUT / f"{name}.schema.json").write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        print(f"wrote docs/contracts/schemas/{name}.schema.json")
