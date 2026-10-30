"""Module 4 — Contradiction detection.

Rule-based numeric comparisons, timestamp overlap checks, LLM-assisted
semantic comparison (optional), missing-evidence detection. Emits conflicts.json.
"""
from app.schemas import ConflictReport, ObservationSet, Timeline


def detect_conflicts(observation_sets: list[ObservationSet], timeline: Timeline) -> ConflictReport:
    raise NotImplementedError("contradiction detection not built yet")
