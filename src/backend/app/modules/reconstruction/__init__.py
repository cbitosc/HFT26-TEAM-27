"""Module 5 — Trajectory reconstruction (backend half).

Turns observations + timeline into per-vehicle tracks on the predefined
intersection. Start from predefined coordinates; the frontend Reconstruction
Viewer animates whatever this returns.
"""
from app.schemas import ConflictReport, ObservationSet, Reconstruction, Timeline


def build_reconstruction(
    observation_sets: list[ObservationSet], timeline: Timeline, conflicts: ConflictReport
) -> Reconstruction:
    raise NotImplementedError("trajectory reconstruction not built yet")
