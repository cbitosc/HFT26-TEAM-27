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

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

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


def _run_stage(stage: str, fn: Callable, args: tuple, fixture: str, model, results: list[StageResult], demo: bool):
    """Returns the module's output, else the fixture (demo mode) or an empty result / None (live mode)."""
    fallback = "served demo fixture" if demo else "no output"
    try:
        out = fn(*args)
        results.append(StageResult(stage=stage, status="real"))
        return out
    except NotImplementedError as e:
        results.append(StageResult(stage=stage, status="fixture" if demo else "skipped", message=f"{e}; {fallback}"))
    except Exception as e:  # a real module failed: keep the pipeline alive
        log.exception("stage %s failed", stage)
        results.append(StageResult(stage=stage, status="error", message=f"{type(e).__name__}: {e}; {fallback}"))
    return store.load_fixture(fixture, model) if demo else _empty(model)


def _extract_all(evidence: list[Evidence], results: list[StageResult]) -> list[ObservationSet]:
    if not evidence:  # demo mode: one pass per source type using bundled fixtures
        for stage, _fn, fixture in EXTRACTORS.values():
            results.append(StageResult(stage=stage, status="fixture", message="no evidence uploaded; demo fixture"))
        return [store.load_fixture(fixture, ObservationSet) for _s, _f, fixture in EXTRACTORS.values()]

    sets: list[ObservationSet] = []
    for ev in evidence:
        stage, fn, fixture = EXTRACTORS[ev.kind]
        out = _run_stage(f"{stage} [{ev.filename}]", fn, (ev,), fixture, ObservationSet, results, demo=False)
        if out is not None:
            sets.append(out)
    return sets


def run_analysis() -> AnalysisRun:
    started = _now()
    results: list[StageResult] = []
    evidence = store.list_evidence()
    demo = not evidence

    observation_sets = _extract_all(evidence, results)
    timeline = _run_stage("3. timeline", build_timeline, (observation_sets,), "timeline.json", Timeline,
                          results, demo)
    conflicts = _run_stage("4. contradictions", detect_conflicts, (observation_sets, timeline),
                           "conflicts.json", ConflictReport, results, demo)
    recon = _run_stage("5. reconstruction", build_reconstruction, (observation_sets, timeline, conflicts),
                       "reconstruction.json", Reconstruction, results, demo)

    run = AnalysisRun(run_id=uuid.uuid4().hex[:8], started_at=started, finished_at=_now(),
                      evidence_count=len(evidence), stages=results)
    store.save_output("observations.json", observation_sets)
    store.save_output("timeline.json", timeline)
    store.save_output("conflicts.json", conflicts)
    store.save_output("reconstruction.json", recon)
    store.save_output("run.json", run)
    return run
