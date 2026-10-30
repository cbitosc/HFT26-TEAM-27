"""Module 1 — Evidence ingestion (dashcam video -> observations).

OpenCV validates the clip and samples frames at ~SAMPLE_FPS; the Featherless
vision model describes each frame as JSON (see vision.py). Per-frame findings
are collapsed into state changes (a signal turning yellow, the ego vehicle
starting to brake, a collision) and returned as Observations with
`t_rel_s = frame / fps + offset` and `frame:<n>` locators.

With no FEATHERLESS_API_KEY the video is still validated and sampled, then
NotImplementedError is raised so the pipeline serves the fixture.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app import config
from app.schemas import Evidence, Observation, ObservationSet, SourceRef, SourceType

from .video import Frame, VideoInfo, encode_jpeg_b64, extract_frames, probe_video, sample_indices
from .vision import FrameAnalysis, VisionClient, VisionError

VISION_MODEL = os.environ.get("FEATHERLESS_VISION_MODEL", "google/gemma-3-27b-it")
SAMPLE_FPS = float(os.environ.get("VIDEO_SAMPLE_FPS", "3"))
MAX_FRAMES = int(os.environ.get("VIDEO_MAX_FRAMES", "90"))
WORKERS = int(os.environ.get("VIDEO_WORKERS", "4"))
# Seconds between the incident reference time and frame 0 (dashcam clock offset).
T0_OFFSET_S = float(os.environ.get("VIDEO_T0_OFFSET_S", "0"))
# Canonical id of the vehicle carrying the dashcam.
EGO_VEHICLE_ID = os.environ.get("VIDEO_EGO_VEHICLE_ID", "V1")
REFERENCE_TIME = datetime(2026, 10, 12, 17, 42, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
MIN_CONFIDENCE = 0.2


def load_frames(evidence: Evidence) -> tuple[VideoInfo, list[Frame]]:
    """Validate the video and return the sampled frames. Raises VideoError if unusable."""
    info = probe_video(evidence.stored_path)
    frames = extract_frames(evidence.stored_path, info, sample_indices(info, SAMPLE_FPS, MAX_FRAMES))
    return info, frames


def analyze_video(evidence: Evidence, client: VisionClient | None = None) -> ObservationSet:
    """Return observations with timestamps and frame-level source refs."""
    info, frames = load_frames(evidence)
    if client is None:
        if not config.FEATHERLESS_API_KEY:
            raise NotImplementedError(
                f"video valid ({info.frame_count} frames @ {info.fps:.1f} fps, {len(frames)} sampled) "
                "but FEATHERLESS_API_KEY is not set; no vision analysis"
            )
        client = VisionClient(config.FEATHERLESS_API_KEY, config.FEATHERLESS_BASE_URL, VISION_MODEL)
        owns_client = True
    else:
        owns_client = False

    warnings: list[str] = []
    if T0_OFFSET_S == 0:
        warnings.append("Dashcam clock offset assumed 0 s relative to the incident reference time (not verified).")
    if len(frames) < len(sample_indices(info, SAMPLE_FPS, MAX_FRAMES)):
        warnings.append(f"Only {len(frames)} sampled frames could be decoded.")

    def describe(frame: Frame) -> tuple[Frame, FrameAnalysis | None, str | None]:
        try:
            return frame, client.describe_frame(encode_jpeg_b64(frame.image), frame.t_s), None
        except VisionError as e:
            return frame, None, str(e)

    try:
        with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as pool:
            results = list(pool.map(describe, frames))
    finally:
        if owns_client:
            client.close()

    failed = [err for _, a, err in results if a is None]
    if frames and len(failed) == len(frames):
        raise RuntimeError(f"vision model failed on all {len(frames)} frames; first error: {failed[0]}")
    if failed:
        warnings.append(f"Vision model failed on {len(failed)}/{len(frames)} frames; those frames were skipped.")

    analysed = [(f, a) for f, a, _ in results if a is not None]
    observations = frames_to_observations(evidence, info, analysed)
    return ObservationSet(
        source_type=SourceType.video,
        evidence_id=evidence.id,
        producer=f"evidence_ingestion:{getattr(client, 'model', VISION_MODEL)}",
        generated_at=datetime.now(timezone.utc),
        observations=observations,
        warnings=warnings,
    )


def _key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def frames_to_observations(
    evidence: Evidence, info: VideoInfo, analysed: list[tuple[Frame, FrameAnalysis]]
) -> list[Observation]:
    """Collapse per-frame findings into state changes; one Observation per change."""
    # Half the sampling interval: the change happened somewhere since the previous sample.
    uncertainty = round(max(1.0 / SAMPLE_FPS, 1.0 / info.fps) / 2, 3)
    last_state: dict[tuple[str, str], str] = {}
    collision_seen = False
    out: list[Observation] = []

    def emit(frame: Frame, action: str, confidence: float, *, vehicle_id=None, description=None, attributes=None) -> None:
        t_rel = round(frame.t_s + T0_OFFSET_S, 3)
        out.append(Observation(
            id=f"vid_{evidence.id}_{len(out) + 1:03d}",
            source_type=SourceType.video,
            source_ref=SourceRef(evidence_id=evidence.id, file=evidence.filename, locator=f"frame:{frame.index}"),
            timestamp=REFERENCE_TIME + timedelta(seconds=t_rel),
            t_rel_s=t_rel,
            time_uncertainty_s=uncertainty,
            vehicle_id=vehicle_id,
            vehicle_description=description,
            action=action,
            attributes=attributes or {},
            confidence=round(confidence, 3),
        ))

    def changed(key: tuple[str, str], state: str) -> bool:
        if last_state.get(key) == state:
            return False
        last_state[key] = state
        return True

    for frame, a in sorted(analysed, key=lambda fa: fa[0].index):
        for s in a.signals:
            if s.state == "unknown" or s.confidence < MIN_CONFIDENCE:
                continue
            if changed(("signal", _key(s.approach)), s.state):
                emit(frame, "signal_state", s.confidence,
                     attributes={"approach": s.approach or "unknown", "signal_state": s.state})

        if a.ego and a.ego.confidence >= MIN_CONFIDENCE and changed(("vehicle", "ego"), a.ego.action):
            attrs = {"ego": True}
            if a.ego.cue:
                attrs["cue"] = a.ego.cue
            emit(frame, a.ego.action, a.ego.confidence, vehicle_id=EGO_VEHICLE_ID,
                 description="ego vehicle (dashcam host)", attributes=attrs)

        for v in a.vehicles:
            if v.confidence < MIN_CONFIDENCE or not _key(v.label):
                continue
            if changed(("vehicle", _key(v.label)), v.action):
                attrs = {"label": v.label}
                if v.direction:
                    attrs["direction"] = v.direction
                emit(frame, v.action, v.confidence, description=v.description or v.label, attributes=attrs)

        c = a.collision
        if c and c.visible and c.confidence >= MIN_CONFIDENCE and not collision_seen:
            collision_seen = True
            vehicles = [EGO_VEHICLE_ID if _key(x) in {"ego", "ego vehicle", "dashcam"} else x for x in c.vehicles]
            attrs = {"vehicles": vehicles}
            if c.impact:
                attrs["impact_side"] = c.impact
            emit(frame, "collision", c.confidence, attributes=attrs)
    return out
