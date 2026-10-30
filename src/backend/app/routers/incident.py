"""Case details: incident id, title, location and the reference time (t = 0)."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from app import config, store
from app.schemas import IncidentInfo, IncidentUpdate

router = APIRouter(prefix="/api/incident", tags=["incident"])


def current() -> IncidentInfo:
    return IncidentInfo(incident_id=config.INCIDENT_ID, title=config.INCIDENT_TITLE, location=config.INCIDENT_LOCATION,
                        reference_time=config.INCIDENT_REFERENCE_TIME,
                        reference_time_confirmed=config.REFERENCE_TIME_CONFIRMED)


@router.get("", response_model=IncidentInfo)
def get_incident() -> IncidentInfo:
    return current()


@router.put("", response_model=IncidentInfo)
def update_incident(update: IncidentUpdate) -> IncidentInfo:
    """Set case details. Re-run the analysis afterwards: times are computed against the reference time."""
    if update.reference_time is not None and update.reference_time.tzinfo is None:
        raise HTTPException(422, "reference_time needs a UTC offset, e.g. 2026-10-12T17:42:00+05:30")
    changes = update.model_dump(exclude_none=True, mode="json")
    config.apply_incident(changes)
    saved = current().model_dump(mode="json")
    config.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    config.incident_file().write_text(json.dumps(
        {k: saved[k] for k in ("incident_id", "title", "location")} |
        ({"reference_time": saved["reference_time"]} if config.REFERENCE_TIME_CONFIRMED else {}), indent=2))
    store.audit("incident_updated", **changes)
    return current()
