import pytest

from app import config


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Every test gets its own empty storage dir; repo storage/ is never touched."""
    monkeypatch.setattr(config, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "RUN_DIR", tmp_path / "runs" / "latest")
    # Case details are module globals changed by PUT /api/incident; restore them after every test.
    for name in ("INCIDENT_ID", "INCIDENT_TITLE", "INCIDENT_LOCATION", "INCIDENT_REFERENCE_TIME",
                 "REFERENCE_TIME_CONFIRMED", "REFERENCE_SOURCE", "REFERENCE_DETAIL", "CASE_VEHICLES", "CASE_SCENE",
                 "LANE_MARKINGS", "GPS_ORIGIN"):
        monkeypatch.setattr(config, name, getattr(config, name))


@pytest.fixture
def demo_case(monkeypatch):
    """The demo scenario's declared case (vehicles V1/V2, junction, clock) from data/case.json."""
    import json
    from pathlib import Path

    case = json.loads((Path(__file__).resolve().parents[1] / "data" / "case.json").read_text())
    config.apply_incident(case)
    return case
