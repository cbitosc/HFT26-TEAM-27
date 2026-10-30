"""Module 1 — Evidence ingestion (dashcam video -> observations).

Owner builds: frame extraction (OpenCV), vehicle/event analysis (Featherless
vision model), structured output. Replace the NotImplementedError below; the
pipeline will stop serving fixtures/video_observations.json automatically.
"""
from app.schemas import Evidence, ObservationSet


def analyze_video(evidence: Evidence) -> ObservationSet:
    """Return observations with timestamps and frame-level source refs."""
    raise NotImplementedError("video ingestion not built yet")
