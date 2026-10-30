"""Runs modules 1→5 in architecture order.

Every stage calls the real module first. What happens when it can't produce
output (raises NotImplementedError, e.g. no Featherless key, or fails) depends
on the mode:

* demo mode (no evidence uploaded): the stage serves its fixture, status "fixture",
  so the dashboard always has the demo incident to show.
* live mode (evidence uploaded): the stage is "skipped" (or "error") and contributes
  nothing; downstream stages get an empty result. Demo data is never mixed into
  results for real evidence.
"""
from __future__ import annotations

import functools
import inspect
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Protocol

from app import config, store
from app.modules.contradictions import detect_conflicts
from app.modules.extraction import extract_sensor, extract_witness
from app.modules.ingestion import analyze_video
from app.modules.reconstruction import build_reconstruction
from app.modules.timeline import build_timeline
from app.schemas import (
    AnalysisRun,
    ConflictReport,
    Evidence,
    ObservationSet,
    Reconstruction,
    Scene,
    SourceType,
    StageResult,
    Timeline,
)

log = logging.getLogger(__name__)

EXTRACTORS: dict[SourceType, tuple[str, Callable[[Evidence], ObservationSet], str]] = {
    SourceType.video: ("1. evidence_ingestion", analyze_video, "video_observations.json"),
    SourceType.witness: ("2. extraction.witness", extract_witness, "witness_observations.json"),
    SourceType.sensor: ("2. extraction.sensor", extract_sensor, "sensor_observations.json"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Tracker(Protocol):
    """Receives live progress; app/jobs.py implements it for the dashboard."""
    def plan(self, stages: list[str]) -> None: ...
    def start(self, stage: str) -> None: ...
    def update(self, stage: str, done: int, total: int, note: str) -> None: ...
    def finish(self, result: StageResult) -> None: ...


class _NoTracker:
    def plan(self, stages): pass
    def start(self, stage): pass
    def update(self, stage, done, total, note): pass
    def finish(self, result): pass


# One run at a time: concurrent runs would race on storage/runs/latest and share the API budget.
RUN_LOCK = threading.Lock()


class RunInProgress(RuntimeError):
    pass


def _empty(model):
    """Placeholder result for a stage that produced nothing in live mode."""
    common = {"incident_id": config.INCIDENT_ID, "generated_at": _now()}
    if model is Timeline:
        return Timeline(**common, reference_time=config.INCIDENT_REFERENCE_TIME, events=[])
    if model is ConflictReport:
        return ConflictReport(**common, conflicts=[])
    if model is Reconstruction:
        return Reconstruction(**common, scene=Scene(extent_m=60, lane_width_m=3.5), t_min_s=0.0, t_max_s=0.0,
                              vehicles=[], collision=None)
    return None


def _run_stage(stage: str, fn: Callable, args: tuple, fixture: str, model, demo: bool,
               tracker: Tracker) -> tuple[object, StageResult]:
    """(output, result): the module's output, else the fixture (demo mode) or an empty result / None (live)."""
    fallback = "served demo fixture" if demo else "no output"
    tracker.start(stage)
    if "progress" in inspect.signature(fn).parameters:  # modules that can report their own progress
        fn = functools.partial(fn, progress=lambda done, total, note: tracker.update(stage, done, total, note))
    try:
        out = fn(*args)
        result = StageResult(stage=stage, status="real")
    except NotImplementedError as e:
        out = None
        result = StageResult(stage=stage, status="fixture" if demo else "skipped", message=f"{e}; {fallback}")
    except Exception as e:  # a real module failed: keep the pipeline alive
        log.exception("stage %s failed", stage)
        out = None
        result = StageResult(stage=stage, status="error", message=f"{type(e).__name__}: {e}; {fallback}")
    if out is None:
        out = store.load_fixture(fixture, model) if demo else _empty(model)
    tracker.finish(result)
    return out, result


def _extract_all(evidence: list[Evidence], results: list[StageResult], tracker: Tracker) -> list[ObservationSet]:
    if not evidence:  # demo mode: one pass per source type using bundled fixtures
        for stage, _fn, fixture in EXTRACTORS.values():
            result = StageResult(stage=stage, status="fixture", message="no evidence uploaded; demo fixture")
            results.append(result)
            tracker.finish(result)
        return [store.load_fixture(fixture, ObservationSet) for _s, _f, fixture in EXTRACTORS.values()]

    # Extractors are independent (and mostly waiting on model APIs), so run them side by side.
    def one(ev: Evidence):
        stage, fn, fixture = EXTRACTORS[ev.kind]
        return _run_stage(_stage_name(ev), fn, (ev,), fixture, ObservationSet, False, tracker)

    with ThreadPoolExecutor(max_workers=min(8, len(evidence))) as pool:
        outcomes = list(pool.map(one, evidence))  # keeps upload order
    sets: list[ObservationSet] = []
    for out, result in outcomes:
        results.append(result)
        if out is not None:
            sets.append(out)
    return sets


def _stage_name(ev: Evidence) -> str:
    return f"{EXTRACTORS[ev.kind][0]} [{ev.filename}]"


def run_analysis(tracker: Tracker | None = None) -> AnalysisRun:
    """Run the whole pipeline. Raises RunInProgress if another run holds the lock."""
    if not RUN_LOCK.acquire(blocking=False):
        raise RunInProgress("an analysis is already running")
    try:
        return _run(tracker or _NoTracker())
    finally:
        RUN_LOCK.release()


def _run(tracker: Tracker) -> AnalysisRun:
    started = _now()
    results: list[StageResult] = []
    evidence = store.list_evidence()
    demo = not evidence
    later = ["3. timeline", "4. contradictions", "5. reconstruction"]
    tracker.plan(([_stage_name(e) for e in evidence] if evidence else [s for s, _f, _x in EXTRACTORS.values()]) + later)

    observation_sets = _extract_all(evidence, results, tracker)
    timeline, r = _run_stage(later[0], build_timeline, (observation_sets,), "timeline.json", Timeline, demo, tracker)
    results.append(r)
    conflicts, r = _run_stage(later[1], detect_conflicts, (observation_sets, timeline), "conflicts.json",
                              ConflictReport, demo, tracker)
    results.append(r)
    recon, r = _run_stage(later[2], build_reconstruction, (observation_sets, timeline, conflicts),
                          "reconstruction.json", Reconstruction, demo, tracker)
    results.append(r)

    run = AnalysisRun(run_id=uuid.uuid4().hex[:8], started_at=started, finished_at=_now(),
                      evidence_count=len(evidence), stages=results)
    store.save_output("observations.json", observation_sets)
    store.save_output("timeline.json", timeline)
    store.save_output("conflicts.json", conflicts)
    store.save_output("reconstruction.json", recon)
    store.save_output("run.json", run)
    return run
