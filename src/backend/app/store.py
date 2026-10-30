"""File-backed storage for uploaded evidence and the latest run's outputs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app import config
from app.schemas import Evidence

M = TypeVar("M", bound=BaseModel)

_INDEX = "evidence_index.json"


def _index_path() -> Path:
    return config.STORAGE_DIR / _INDEX


def list_evidence() -> list[Evidence]:
    path = _index_path()
    if not path.exists():
        return []
    return [Evidence.model_validate(e) for e in json.loads(path.read_text())]


def add_evidence(evidence: Evidence) -> None:
    items = list_evidence() + [evidence]
    config.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    _index_path().write_text(json.dumps([e.model_dump(mode="json") for e in items], indent=2))


def delete_evidence(evidence_id: str) -> bool:
    items = list_evidence()
    keep = [e for e in items if e.id != evidence_id]
    if len(keep) == len(items):
        return False
    for e in items:
        if e.id == evidence_id:
            Path(e.stored_path).unlink(missing_ok=True)
    _index_path().write_text(json.dumps([e.model_dump(mode="json") for e in keep], indent=2))
    return True


def load_fixture(name: str, model: type[M]) -> M:
    return model.model_validate_json((config.FIXTURES_DIR / name).read_text())


def save_output(name: str, data: BaseModel | list[BaseModel]) -> None:
    config.RUN_DIR.mkdir(parents=True, exist_ok=True)
    if isinstance(data, list):
        payload = [d.model_dump(mode="json") for d in data]
    else:
        payload = data.model_dump(mode="json")
    (config.RUN_DIR / name).write_text(json.dumps(payload, indent=2))


def load_output(name: str) -> object | None:
    path = config.RUN_DIR / name
    return json.loads(path.read_text()) if path.exists() else None
