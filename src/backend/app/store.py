"""File-backed storage: uploaded evidence, run outputs (latest + history) and the audit log."""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app import config
from app.schemas import AuditEvent, Evidence

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


# ------------------------------------------------------------------ run history

MAX_RUNS_KEPT = 50


def _history_dir() -> Path:
    return config.RUN_DIR.parent  # storage/runs/<run_id>/ next to storage/runs/latest/


def archive_latest(run_id: str) -> None:
    """Copy the latest outputs to storage/runs/<run_id>/ and prune the oldest beyond MAX_RUNS_KEPT."""
    dest = _history_dir() / run_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(config.RUN_DIR, dest)
    runs = sorted((d for d in _history_dir().iterdir() if d.is_dir() and d.name != "latest"),
                  key=lambda d: d.stat().st_mtime)
    for old in runs[:-MAX_RUNS_KEPT]:
        shutil.rmtree(old, ignore_errors=True)


def list_runs() -> list[dict]:
    out = []
    for d in _history_dir().iterdir() if _history_dir().exists() else []:
        if d.is_dir() and d.name != "latest" and (d / "run.json").exists():
            out.append(json.loads((d / "run.json").read_text()))
    return sorted(out, key=lambda r: r["started_at"], reverse=True)


def load_run_output(run_id: str, name: str) -> object | None:
    if "/" in run_id or ".." in run_id:
        return None
    path = _history_dir() / run_id / name
    return json.loads(path.read_text()) if path.exists() else None


# ------------------------------------------------------------------ audit log

_audit_lock = threading.Lock()


def audit(event: str, **data) -> None:
    """Append one event to storage/audit.jsonl. Never rewritten, only appended."""
    entry = AuditEvent(at=datetime.now(timezone.utc), event=event, data=data)
    config.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    with _audit_lock, open(config.STORAGE_DIR / "audit.jsonl", "a") as f:
        f.write(entry.model_dump_json() + "\n")


def read_audit(limit: int = 200) -> list[AuditEvent]:
    path = config.STORAGE_DIR / "audit.jsonl"
    if not path.exists():
        return []
    lines = path.read_text().splitlines()[-limit:]
    return [AuditEvent.model_validate_json(line) for line in reversed(lines) if line.strip()]
