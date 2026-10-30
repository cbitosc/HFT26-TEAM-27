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
                 "REFERENCE_TIME_CONFIRMED"):
        monkeypatch.setattr(config, name, getattr(config, name))
