"""Run the pipeline and read its outputs.

Before the first run, each GET serves the matching fixture so the dashboard
always has something to render.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import store
from app.jobs import jobs
from app.pipeline import RunInProgress, run_analysis
from app.schemas import AnalysisJob, AnalysisRun, ConflictReport, ObservationSet, Reconstruction, Timeline

router = APIRouter(prefix="/api", tags=["analysis"])

_OBSERVATION_FIXTURES = ["video_observations.json", "witness_observations.json", "sensor_observations.json"]


@router.post("/analysis/run", response_model=AnalysisRun)
def run() -> AnalysisRun:
    """Synchronous run (scripts, tests). The dashboard uses the background job endpoints."""
    try:
        return run_analysis()
    except RunInProgress as e:
        raise HTTPException(409, str(e))


@router.post("/analysis/jobs", response_model=AnalysisJob, status_code=202)
def start_job() -> AnalysisJob:
    """Start a background run, or return the one already running."""
    return jobs.start()


@router.get("/analysis/jobs/current", response_model=AnalysisJob | None)
def current_job():
    return jobs.current()


@router.get("/analysis/latest", response_model=AnalysisRun | None)
def latest_run():
    return store.load_output("run.json")


@router.get("/observations", response_model=list[ObservationSet])
def observations():
    out = store.load_output("observations.json")
    return out if out is not None else [store.load_fixture(f, ObservationSet) for f in _OBSERVATION_FIXTURES]


@router.get("/timeline", response_model=Timeline)
def timeline():
    return store.load_output("timeline.json") or store.load_fixture("timeline.json", Timeline)


@router.get("/conflicts", response_model=ConflictReport)
def conflicts():
    return store.load_output("conflicts.json") or store.load_fixture("conflicts.json", ConflictReport)


@router.get("/reconstruction", response_model=Reconstruction)
def reconstruction():
    return store.load_output("reconstruction.json") or store.load_fixture("reconstruction.json", Reconstruction)
