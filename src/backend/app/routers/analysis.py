"""Run the pipeline and read its outputs.

Before the first run, each GET serves the matching fixture so the dashboard
always has something to render.
"""
from __future__ import annotations

from fastapi import APIRouter

from app import store
from app.pipeline import run_analysis
from app.schemas import AnalysisRun, ConflictReport, ObservationSet, Reconstruction, Timeline

router = APIRouter(prefix="/api", tags=["analysis"])

_OBSERVATION_FIXTURES = ["video_observations.json", "witness_observations.json", "sensor_observations.json"]


@router.post("/analysis/run", response_model=AnalysisRun)
def run() -> AnalysisRun:
    return run_analysis()


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
