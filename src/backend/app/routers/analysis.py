"""Run the pipeline and read its outputs.

Before the first run, each GET serves the matching fixture so the dashboard
always has something to render.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app import store
from app.report import build_report
from app.jobs import jobs
from app.pipeline import RunInProgress, run_analysis
from app.schemas import (AnalysisJob, AnalysisRun, AuditEvent, ConflictReport, IncidentReport, ObservationSet,
                         Reconstruction, Timeline)

RUN_ARTIFACTS = {"run": "run.json", "observations": "observations.json", "timeline": "timeline.json",
                 "conflicts": "conflicts.json", "reconstruction": "reconstruction.json",
                 "event_analysis": "event_analysis.json"}

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


@router.get("/runs", response_model=list[AnalysisRun])
def runs():
    """Every kept run, newest first (the last 50)."""
    return store.list_runs()


@router.get("/runs/{run_id}/report", response_model=IncidentReport)
def run_report(run_id: str, format: str = "json", narrative: bool = True):
    return _report(run_id, format, narrative)


@router.get("/runs/{run_id}/{artifact}")
def run_artifact(run_id: str, artifact: str):
    """One output of a past run: run, observations, timeline, conflicts or reconstruction."""
    if artifact not in RUN_ARTIFACTS:
        raise HTTPException(404, f"Unknown artifact; one of {', '.join(RUN_ARTIFACTS)}")
    out = store.load_run_output(run_id, RUN_ARTIFACTS[artifact])
    if out is None:
        raise HTTPException(404, "Run not found")
    return out


def _report(run_id: str | None, format: str, narrative: bool):
    rep = build_report(run_id, narrative=narrative)
    if rep is None:
        raise HTTPException(404, "No analysis run yet" if run_id is None else "Run not found")
    if format == "markdown":
        return PlainTextResponse(rep.markdown, media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'inline; filename="report-{rep.run_id}.md"'})
    return rep


@router.get("/report", response_model=IncidentReport)
def report(format: str = "json", narrative: bool = True):
    """Report of the latest run. format=markdown returns the document; narrative=false skips the LLM summary."""
    return _report(None, format, narrative)


@router.get("/audit", response_model=list[AuditEvent])
def audit_log(limit: int = 200):
    """Append-only log of uploads, rejections, deletions and runs, newest first."""
    return store.read_audit(max(1, min(limit, 5000)))


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


@router.get("/event-analysis", response_model=Reconstruction | None)
def event_analysis():
    """Camera footage with detections and image-space tracks (pixels), or null without video tracks."""
    return store.load_output("event_analysis.json")


@router.get("/reconstruction", response_model=Reconstruction)
def reconstruction():
    return store.load_output("reconstruction.json") or store.load_fixture("reconstruction.json", Reconstruction)
