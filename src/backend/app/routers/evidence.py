"""Evidence upload, validation and storage (feeds modules 1–2)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
from fastapi import APIRouter, HTTPException, Response, UploadFile

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


@router.get("/{evidence_id}/frames/{index}", response_class=Response,
            responses={200: {"content": {"image/jpeg": {}}}})
def video_frame(evidence_id: str, index: int, width: int = 480) -> Response:
    """One frame of an uploaded video as a JPEG (thumbnails for `frame:<n>` source refs)."""
    ev = next((e for e in store.list_evidence() if e.id == evidence_id), None)
    if ev is None or ev.kind != SourceType.video:
        raise HTTPException(404, "Video evidence not found")
    cap = cv2.VideoCapture(ev.stored_path)
    try:
        if index < 0 or index >= int(cap.get(cv2.CAP_PROP_FRAME_COUNT)):
            raise HTTPException(404, "Frame out of range")
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = cap.read()
    finally:
        cap.release()
    if not ok:
        raise HTTPException(500, "Could not decode frame")
    width = max(64, min(width, 1280))
    h, w = image.shape[:2]
    if w > width:
        image = cv2.resize(image, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
