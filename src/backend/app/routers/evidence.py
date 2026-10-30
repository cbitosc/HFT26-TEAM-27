"""Evidence upload, validation and storage (feeds modules 1–2)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from app import config, store
from app.schemas import Evidence, SourceType

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


def _kind_for(filename: str) -> SourceType:
    ext = Path(filename).suffix.lower()
    for kind, exts in config.ALLOWED_EXTENSIONS.items():
        if ext in exts:
            return SourceType(kind)
    allowed = sorted(e for exts in config.ALLOWED_EXTENSIONS.values() for e in exts)
    raise HTTPException(415, f"Unsupported file type '{ext}'. Allowed: {', '.join(allowed)}")


@router.get("", response_model=list[Evidence])
def list_evidence() -> list[Evidence]:
    return store.list_evidence()


@router.post("", response_model=Evidence, status_code=201)
async def upload_evidence(file: UploadFile) -> Evidence:
    if not file.filename:
        raise HTTPException(400, "Missing filename")
    kind = _kind_for(file.filename)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")

    evidence_id = f"ev_{uuid.uuid4().hex[:8]}"
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.UPLOAD_DIR / f"{evidence_id}{Path(file.filename).suffix.lower()}"
    dest.write_bytes(data)

    evidence = Evidence(
        id=evidence_id, kind=kind, filename=file.filename, content_type=file.content_type,
        size_bytes=len(data), stored_path=str(dest), uploaded_at=datetime.now(timezone.utc),
    )
    store.add_evidence(evidence)
    return evidence


@router.delete("/{evidence_id}", status_code=204)
def delete_evidence(evidence_id: str) -> None:
    if not store.delete_evidence(evidence_id):
        raise HTTPException(404, "Evidence not found")
