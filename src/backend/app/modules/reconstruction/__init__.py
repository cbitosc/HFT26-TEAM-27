"""Module 5 — Trajectory reconstruction (backend half).

Turns observations + timeline into per-vehicle tracks on the predefined
intersection. Start from predefined coordinates; the frontend Reconstruction
Viewer animates whatever this returns.
"""
from app.schemas import ConflictReport, ObservationSet, Reconstruction, Timeline

from .builder import reconstruct
from .camera import reconstruct_from_camera


def build_reconstruction(
    observation_sets: list[ObservationSet], timeline: Timeline, conflicts: ConflictReport
) -> Reconstruction:
    metric = reconstruct(observation_sets, timeline, conflicts)
    if metric.vehicles:
        return metric  # positions in metres beat pixels
    return reconstruct_from_camera(observation_sets, timeline) or metric
