"""Runs modules 1→5 in architecture order.

Walking-skeleton rule: every stage calls the real module first. If the module
raises NotImplementedError, the stage serves its fixture instead and is
reported as status="fixture". Building a module therefore needs no change
here — once it stops raising, its output flows through and the dashboard
shows the stage as "real".
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

from app import store
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


def _run_stage(stage: str, fn: Callable, args: tuple, fixture: str, model, results: list[StageResult]):
    try:
        out = fn(*args)
        results.append(StageResult(stage=stage, status="real"))
        return out
    except NotImplementedError as e:
        results.append(StageResult(stage=stage, status="fixture", message=str(e)))
    except Exception as e:  # a real module failed: keep the pipeline alive on its fixture
        log.exception("stage %s failed", stage)
        results.append(StageResult(stage=stage, status="error", message=f"{type(e).__name__}: {e}; served fixture"))
    return store.load_fixture(fixture, model)


def _extract_all(evidence: list[Evidence], results: list[StageResult]) -> list[ObservationSet]:
    sets: list[ObservationSet] = []
    fixture_served: set[SourceType] = set()
    # No uploads yet -> demo mode: one pass per source type using bundled fixtures.
    kinds = [e.kind for e in evidence] or list(EXTRACTORS)
    items: list[Evidence | None] = list(evidence) or [None] * len(kinds)

    for kind, ev in zip(kinds, items):
        stage, fn, fixture = EXTRACTORS[kind]
        if ev is None:
            results.append(StageResult(stage=stage, status="fixture", message="no evidence uploaded; demo fixture"))
            sets.append(store.load_fixture(fixture, ObservationSet))
            continue
        before = len(results)
        out = _run_stage(f"{stage} [{ev.filename}]", fn, (ev,), fixture, ObservationSet, results)
        if results[before].status == "real" or kind not in fixture_served:
            sets.append(out)
        if results[before].status != "real":
            fixture_served.add(kind)  # same fixture would just duplicate observations
    return sets


def run_analysis() -> AnalysisRun:
    started = _now()
    results: list[StageResult] = []
    evidence = store.list_evidence()

    observation_sets = _extract_all(evidence, results)
    timeline = _run_stage("3. timeline", build_timeline, (observation_sets,), "timeline.json", Timeline, results)
    conflicts = _run_stage("4. contradictions", detect_conflicts, (observation_sets, timeline),
                           "conflicts.json", ConflictReport, results)
    recon = _run_stage("5. reconstruction", build_reconstruction, (observation_sets, timeline, conflicts),
                       "reconstruction.json", Reconstruction, results)

    run = AnalysisRun(run_id=uuid.uuid4().hex[:8], started_at=started, finished_at=_now(),
                      evidence_count=len(evidence), stages=results)
    store.save_output("observations.json", observation_sets)
    store.save_output("timeline.json", timeline)
    store.save_output("conflicts.json", conflicts)
    store.save_output("reconstruction.json", recon)
    store.save_output("run.json", run)
    return run
