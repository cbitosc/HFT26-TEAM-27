"""Module 3 — Timeline reconstruction.

Collect observations, normalise timestamps, sort, correlate observations of
the same event, group, emit timeline.json.
"""
from app.schemas import ObservationSet, Timeline


def build_timeline(observation_sets: list[ObservationSet]) -> Timeline:
    raise NotImplementedError("timeline reconstruction not built yet")
