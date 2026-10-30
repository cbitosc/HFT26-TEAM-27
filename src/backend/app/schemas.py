"""Integration contract: the data shapes every module produces and consumes.

These models ARE the contract. Fixtures in /fixtures must validate against
them (tests/test_fixtures.py enforces this) and every real module must
return instances of them. Changing a field here is a contract change — see
docs/contracts/INTEGRATION_CONTRACT.md for the rules.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0.2"


# ---------------------------------------------------------------- evidence

class SourceType(str, Enum):
    video = "video"
    witness = "witness"
    sensor = "sensor"


class Evidence(BaseModel):
    """One uploaded file. Created by the upload endpoint, read by modules 1–2."""
    id: str
    kind: SourceType
    filename: str
    content_type: str | None = None
    size_bytes: int
    stored_path: str
    uploaded_at: datetime


# ------------------------------------------------- modules 1–2: observations

class SourceRef(BaseModel):
    """Where in the original evidence an observation came from."""
    evidence_id: str
    file: str
    locator: str = Field(description="frame:<n> | line:<n> | row:<n> | sentence:<n>")


class Observation(BaseModel):
    """The common observation schema shared by video, witness and sensor."""
    id: str
    source_type: SourceType
    source_ref: SourceRef
    timestamp: datetime | None = Field(None, description="Absolute time if known")
    t_rel_s: float | None = Field(None, description="Seconds relative to incident reference time")
    time_uncertainty_s: float = Field(0.0, ge=0)
    vehicle_id: str | None = Field(None, description="Canonical id (V1, V2, ...) or null if unresolved")
    vehicle_description: str | None = None
    action: str = Field(description="e.g. moving, braking, entering_intersection, collision, signal_state, stopped")
    attributes: dict[str, Any] = Field(default_factory=dict, description="speed_kmh, heading_deg, signal_state, x_m, y_m, ...")
    confidence: float = Field(ge=0, le=1)
    raw_text: str | None = Field(None, description="Verbatim quote for witness-derived observations")


class ObservationSet(BaseModel):
    """Output of one extractor run over one piece of evidence."""
    schema_version: str = SCHEMA_VERSION
    source_type: SourceType
    evidence_id: str
    producer: str
    generated_at: datetime
    observations: list[Observation]
    warnings: list[str] = Field(default_factory=list)


# ------------------------------------------------------- module 3: timeline

class TimelineEvent(BaseModel):
    id: str
    t_start_s: float
    t_end_s: float
    timestamp: datetime | None = None
    label: str
    category: Literal["vehicle_motion", "signal", "collision", "post_collision", "other"]
    vehicle_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str]
    source_types: list[SourceType]
    confidence: float = Field(ge=0, le=1)
    uncertainty_s: float = Field(0.0, ge=0)


class Timeline(BaseModel):
    schema_version: str = SCHEMA_VERSION
    incident_id: str
    reference_time: datetime
    generated_at: datetime
    events: list[TimelineEvent]


# ------------------------------------------------- module 4: contradictions

class ConflictType(str, Enum):
    numeric_mismatch = "numeric_mismatch"
    timestamp_overlap = "timestamp_overlap"
    semantic = "semantic"
    missing_evidence = "missing_evidence"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Conflict(BaseModel):
    id: str
    type: ConflictType
    severity: Severity
    title: str
    description: str
    observation_ids: list[str]
    t_range_s: tuple[float, float] | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ConflictReport(BaseModel):
    schema_version: str = SCHEMA_VERSION
    incident_id: str
    generated_at: datetime
    conflicts: list[Conflict]


# ------------------------------------------------- module 5: reconstruction

class TrackPoint(BaseModel):
    t_s: float
    x_m: float
    y_m: float
    heading_deg: float
    speed_kmh: float | None = None


class VehicleTrack(BaseModel):
    vehicle_id: str
    label: str
    color: str
    description: str | None = None
    track: list[TrackPoint]
    source_observation_ids: list[str] = Field(default_factory=list)


class CollisionMarker(BaseModel):
    t_s: float
    x_m: float
    y_m: float
    confidence: float = Field(ge=0, le=1)
    supporting_observation_ids: list[str]


class Scene(BaseModel):
    type: Literal["four_way_intersection"] = "four_way_intersection"
    extent_m: float = Field(description="Half-width of the square view around the origin")
    lane_width_m: float
    roads: dict[str, str] = Field(default_factory=dict, description="axis -> road name, e.g. {'ns': 'Main Rd'}")


class Reconstruction(BaseModel):
    schema_version: str = SCHEMA_VERSION
    incident_id: str
    generated_at: datetime
    scene: Scene
    t_min_s: float
    t_max_s: float
    vehicles: list[VehicleTrack]
    collision: CollisionMarker | None = None


# ------------------------------------------------------------ pipeline/API

class StageResult(BaseModel):
    stage: str
    status: Literal["real", "fixture", "skipped", "error"]
    message: str = ""


class AnalysisRun(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime
    evidence_count: int
    stages: list[StageResult]


class StageProgress(BaseModel):
    """Live state of one stage while a run is in progress."""
    stage: str
    status: Literal["pending", "running", "real", "fixture", "skipped", "error"] = "pending"
    done: int = 0
    total: int = 0
    note: str = ""


class AnalysisJob(BaseModel):
    """A background analysis run, polled by the dashboard (GET /api/analysis/jobs/current)."""
    job_id: str
    state: Literal["running", "done", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    stages: list[StageProgress]
    run: AnalysisRun | None = None
    error: str | None = None
