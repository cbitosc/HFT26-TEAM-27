"""Module 1 — Evidence ingestion (dashcam or fixed-camera video -> observations).

OpenCV validates the clip and picks up to MAX_FRAMES keyframes: an even baseline plus
the moments of most visual change (video.select_keyframes, scanned at SAMPLE_FPS). The
Featherless vision model describes each keyframe as JSON (see vision.py), WORKERS calls
at a time; answers are cached per file/model/prompt/frame under storage/cache/vision,
so re-running the same video costs nothing. Per-frame findings
are collapsed into state changes (a signal turning yellow, the ego vehicle
starting to brake, a collision) and returned as Observations with
`t_rel_s = frame / fps + offset` and `frame:<n>` locators.

Camera mode (VIDEO_CAMERA_MODE=auto|dashcam|fixed, default auto): a dashcam has an
ego vehicle; fixed roadside cameras (CCTV, possibly a grid of views) do not, and only
notable vehicles, hazards, visible signals and collisions are reported. "auto" decides
from how much of the background changes between sampled frames (see video.py).

With no FEATHERLESS_API_KEY the video is still validated and sampled, then
NotImplementedError is raised so the pipeline skips the stage.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from app import config
from app.schemas import Evidence, Observation, ObservationSet, SourceRef, SourceType

from .video import (MOVING_CAMERA_SHARE, Frame, Track, VideoInfo, camera_motion_share, detect_grid,
                    encode_jpeg_b64, extract_frames, probe_video, select_keyframes, static_regions, track_vehicles,
                    with_followers)
from .vision import PROMPTS, CameraMode, FrameAnalysis, VisionClient, VisionError

# Ungated on Featherless; google/gemma-3-27b-it is gated (needs HuggingFace OAuth, else 403).
VISION_MODEL = os.environ.get("FEATHERLESS_VISION_MODEL", "Qwen/Qwen3-VL-30B-A3B-Instruct")
SAMPLE_FPS = float(os.environ.get("VIDEO_SAMPLE_FPS", "4"))   # cheap OpenCV scan rate for keyframe picking
MAX_FRAMES = int(os.environ.get("VIDEO_MAX_FRAMES", "24"))    # vision-model calls per video
# Parallel model calls. Featherless plans cap concurrency (Qwen3-VL-30B costs 2 of a plan's units; pay-per-request plans allow 100); 429s are retried.
WORKERS = int(os.environ.get("VIDEO_WORKERS", "24"))  # = MAX_FRAMES: one round of calls
# Seconds between the incident reference time and frame 0 (dashcam clock offset).
T0_OFFSET_S = float(os.environ.get("VIDEO_T0_OFFSET_S", "0"))
# Canonical id of the vehicle carrying the dashcam.
EGO_VEHICLE_ID = os.environ.get("VIDEO_EGO_VEHICLE_ID", "V1")
MIN_CONFIDENCE = 0.2
CAMERA_MODE = os.environ.get("VIDEO_CAMERA_MODE", "auto").strip().lower()
TRACKING = os.environ.get("VIDEO_TRACKING", "1") != "0"  # fixed cameras: OpenCV motion tracks
MAX_TRACKS = int(os.environ.get("VIDEO_MAX_TRACKS", "200"))
PAIR_GAP_S = float(os.environ.get("VIDEO_PAIR_GAP_S", "1.0"))  # fixed cameras: second image this much later
# Fixed cameras: the model often misreads flowing traffic as "stopped", so every stopped vehicle must
# come with a box and that patch of the image must stay unchanged a few seconds later (video.static_regions).
# Momentary actions can't be judged from snapshots seconds apart, so fixed mode drops them.
TRANSIENT_ACTIONS = {"braking", "accelerating", "changing_lanes", "turning", "moving",
                     "entering_intersection", "crossing_intersection"}
TRANSIENT_HAZARDS = {"sudden_braking", "swerve", "other"}


def detect_camera_mode(frames: list[Frame]) -> tuple[CameraMode, str]:
    """(mode, how it was decided) from VIDEO_CAMERA_MODE or the background change between frames."""
    if CAMERA_MODE in ("dashcam", "fixed"):
        return CAMERA_MODE, f"set by VIDEO_CAMERA_MODE={CAMERA_MODE}"
    share = camera_motion_share(frames)
    if share is None:
        return "dashcam", "too few frames to judge; assumed dashcam"
    mode: CameraMode = "dashcam" if share > MOVING_CAMERA_SHARE else "fixed"
    return mode, (f"auto-detected: {share:.0%} of the frame changes between samples "
                  f"({'>' if mode == 'dashcam' else '<='} {MOVING_CAMERA_SHARE:.0%} threshold)")


def load_frames(evidence: Evidence) -> tuple[VideoInfo, list[Frame]]:
    """Validate the video and return its keyframes. Raises VideoError if unusable."""
    info = probe_video(evidence.stored_path)
    frames = extract_frames(evidence.stored_path, info,
                            select_keyframes(evidence.stored_path, info, MAX_FRAMES, SAMPLE_FPS))
    return info, frames


class _Cache:
    """Per-frame model answers on disk, keyed by file content, model, prompt and frame index."""

    def __init__(self, evidence: Evidence, model: str, mode: CameraMode, pair_gap_s: float = 0.0):
        h = hashlib.sha256()
        with open(evidence.stored_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        prompt = hashlib.sha256(PROMPTS[mode].encode()).hexdigest()[:8]
        safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", model)
        self.dir = Path(config.STORAGE_DIR) / "cache" / "vision" / h.hexdigest()[:16] / (f"{safe_model}_{mode}_{prompt}" + (f"_pair{pair_gap_s:g}" if pair_gap_s else ""))

    def get(self, frame: Frame) -> FrameAnalysis | None:
        path = self.dir / f"{frame.index}.json"
        try:
            return FrameAnalysis.model_validate_json(path.read_text()) if path.exists() else None
        except ValueError:
            return None

    def put(self, frame: Frame, analysis: FrameAnalysis) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{frame.index}.json").write_text(analysis.model_dump_json())


def analyze_video(evidence: Evidence, client: VisionClient | None = None,
                  progress: Callable[[int, int, str], None] | None = None) -> ObservationSet:
    """Return observations with timestamps and frame-level source refs.

    `progress(done, total, note)` is called as keyframes are analysed (for the dashboard).
    """
    report = progress or (lambda done, total, note: None)
    report(0, 0, "scanning video for keyframes")
    info, frames = load_frames(evidence)

    # Judge the camera on consecutive frames: keyframes are seconds apart, so traffic alone
    # would change most of the picture between them and every camera would look like a dashcam.
    step = max(1, round(info.fps / SAMPLE_FPS))
    burst = extract_frames(evidence.stored_path, info, [i * step for i in range(min(16, info.frame_count // step))])
    mode, why = detect_camera_mode(burst or frames)
    warnings: list[str] = [f"Camera mode: {mode} ({why})."]
    if T0_OFFSET_S == 0:
        warnings.append(f"{'Dashcam' if mode == 'dashcam' else 'Camera'} clock offset assumed 0 s relative to "
                        "the incident reference time (not verified).")

    # Fixed cameras: vehicle trajectories from the pixels themselves (OpenCV only, no model, no key).
    track_obs: list[Observation] = []
    views: list[tuple[int, int, int, int]] = []
    if mode == "fixed" and TRACKING and frames:
        report(0, 0, "tracking vehicles")
        views = detect_grid(frames[0].image)
        tracks = track_vehicles(evidence.stored_path, info, views)
        track_obs = tracks_to_observations(evidence, info, tracks, views)
        warnings.append(f"{len(track_obs)} vehicle track(s) from motion in {len(views)} camera view(s) "
                        "(pixel coordinates; stationary vehicles are not tracked by motion).")

    if client is None:
        if not config.FEATHERLESS_API_KEY:
            if track_obs:
                warnings.append("FEATHERLESS_API_KEY is not set: vehicle tracks only, no vision-model findings.")
                return ObservationSet(source_type=SourceType.video, evidence_id=evidence.id,
                                      producer="evidence_ingestion:opencv-tracking",
                                      generated_at=datetime.now(timezone.utc), observations=track_obs,
                                      warnings=warnings)
            raise NotImplementedError(
                f"video valid ({info.frame_count} frames @ {info.fps:.1f} fps, {len(frames)} sampled) "
                "but FEATHERLESS_API_KEY is not set; no vision analysis"
            )
        client = VisionClient(config.FEATHERLESS_API_KEY, config.FEATHERLESS_BASE_URL, VISION_MODEL)
        owns_client = True
    else:
        owns_client = False

    if mode == "fixed" and PAIR_GAP_S > 0:
        frames = with_followers(evidence.stored_path, info, frames, PAIR_GAP_S)
    warnings.append(f"{len(frames)} keyframes analysed out of {info.frame_count} frames "
                    f"({info.duration_s:.0f} s @ {info.fps:g} fps).")
    model = getattr(client, "model", VISION_MODEL)
    cache = _Cache(evidence, model, mode, PAIR_GAP_S if mode == "fixed" else 0.0)

    def describe(frame: Frame) -> tuple[Frame, FrameAnalysis | None, str | None]:
        if (hit := cache.get(frame)) is not None:
            return frame, hit, None
        try:
            if frame.next_image is not None:
                analysis = client.describe_frame(encode_jpeg_b64(frame.image), frame.t_s, mode=mode,
                                                 next_jpeg_b64=encode_jpeg_b64(frame.next_image), gap_s=frame.next_gap_s)
            else:
                analysis = client.describe_frame(encode_jpeg_b64(frame.image), frame.t_s, mode=mode)
        except VisionError as e:
            return frame, None, str(e)
        cache.put(frame, analysis)
        return frame, analysis, None

    results = []
    report(0, len(frames), f"{mode} mode: analysing {len(frames)} keyframes")
    try:
        with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as pool:
            for fut in as_completed([pool.submit(describe, f) for f in frames]):
                results.append(fut.result())
                report(len(results), len(frames), f"{mode} mode: analysed {len(results)}/{len(frames)} keyframes")
    finally:
        if owns_client:
            client.close()

    failed = [err for _, a, err in results if a is None]
    if frames and len(failed) == len(frames):
        raise RuntimeError(f"vision model failed on all {len(frames)} frames; first error: {failed[0]}")
    if failed:
        warnings.append(f"Vision model failed on {len(failed)}/{len(frames)} frames; those frames were skipped.")

    analysed = [(f, a) for f, a, _ in results if a is not None]
    persistence: dict = {}
    if mode == "fixed":
        analysed, persistence, dropped = aggregate_fixed(
            analysed, lambda checks: static_regions(evidence.stored_path, info, checks))
        if dropped["stopped"]:
            warnings.append(f"{dropped['stopped']} 'stopped' report(s) dropped: the vehicle's patch of the image "
                            "changed 2-4 s later (it was moving) or no box was given to check.")
        if dropped["transient"]:
            warnings.append(f"{dropped['transient']} momentary claim(s) (braking, lane changes, swerves) dropped: "
                            "they can't be judged reliably from keyframes seconds apart.")
    observations = frames_to_observations(evidence, info, analysed, mode, persistence) + track_obs
    return ObservationSet(
        source_type=SourceType.video,
        evidence_id=evidence.id,
        producer=f"evidence_ingestion:{model}",
        generated_at=datetime.now(timezone.utc),
        observations=observations,
        warnings=warnings,
    )


def tracks_to_observations(evidence: Evidence, info: VideoInfo, tracks: list[Track],
                           views: list[tuple[int, int, int, int]]) -> list[Observation]:
    """One compact observation per track (action "track"): all its points ride in attributes.points."""
    longest = sorted(tracks, key=lambda tr: tr.points[-1][0] - tr.points[0][0], reverse=True)[:MAX_TRACKS]
    out = []
    for n, tr in enumerate(sorted(longest, key=lambda tr: (tr.points[0][0], tr.view)), start=1):
        (t0, x0, y0), (t1, x1, y1) = tr.points[0], tr.points[-1]
        dist = sum(math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(tr.points, tr.points[1:]))
        out.append(Observation(
            id=f"vid_{evidence.id}_trk_{n:03d}",
            source_type=SourceType.video,
            source_ref=SourceRef(evidence_id=evidence.id, file=evidence.filename,
                                 locator=f"frames:{tr.first_frame}-{tr.last_frame}"),
            timestamp=config.INCIDENT_REFERENCE_TIME + timedelta(seconds=t0 + T0_OFFSET_S),
            t_rel_s=round(t0 + T0_OFFSET_S, 3),
            time_uncertainty_s=round(1 / info.fps, 3),
            vehicle_id=None,
            vehicle_description=f"moving vehicle, camera view {tr.view}",
            action="track",
            attributes={"track_id": f"T{tr.view}-{n:03d}", "view": tr.view, "camera": "fixed", "units": "px",
                        "frame_size": [info.width, info.height], "fps": round(info.fps, 3),
                        "view_box": list(views[tr.view - 1]), "grid": [list(v) for v in views],
                        "direction": "towards camera" if y1 > y0 else "away from camera",
                        "mean_speed_px_s": round(dist / max(t1 - t0, 1e-6), 1),
                        "points": [[round(t + T0_OFFSET_S, 3), x, y] for t, x, y in tr.points]},
            confidence=0.6,
        ))
    return out


def view_of_box(box: list[float] | None, views: int | None) -> int | None:
    """Camera view (1-based, left-to-right, top-to-bottom) containing the box centre, for a grid of
    `views` cameras laid out as near-square as possible (4 -> 2x2, 2 -> 2x1, 6 -> 3x2)."""
    if not box or not views or views < 2:
        return None
    cols = math.ceil(math.sqrt(views))
    rows = math.ceil(views / cols)
    cx, cy = (box[0] + box[2]) / 2000, (box[1] + box[3]) / 2000
    view = min(rows - 1, int(cy * rows)) * cols + min(cols - 1, int(cx * cols)) + 1
    return view if view <= views else None


def aggregate_fixed(analysed: list[tuple[Frame, FrameAnalysis]],
                    verify: Callable[[list[tuple[int, list[float]]]], list[dict]] | None = None):
    """Fixed cameras: keep what the pixels confirm, drop what can't be judged from snapshots.

    * stopped vehicles: kept only if they come with a box and `verify` finds that patch static
      2-4 s later. The same spot seen in several keyframes becomes one vehicle (first label wins).
    * stopped_in_lane hazards: folded into the verified stopped vehicles (dropped as separate items)
    * momentary vehicle actions and hazards (braking, lane change, swerve, "other"): dropped
    * other hazards (wrong way, debris, pedestrian): kept if reported in >= 2 keyframes of that view
    * collisions: always kept
    Returns (filtered analyses, {(view, label key): (sightings, first_t, last_t)}, dropped counts).
    """
    timed = sorted(analysed, key=lambda fa: fa[0].t_s)
    # The model's view numbers slip; where a box sits in the camera grid does not.
    timed = [(f, a.model_copy(update={"vehicles": [
        v.model_copy(update={"view": view_of_box(v.box, a.views) or v.view}) if v.box else v for v in a.vehicles]}))
        for f, a in timed]
    stops = [(f, v) for f, a in timed for v in a.vehicles if v.action == "stopped" and v.box]
    verdicts = verify([(f.index, v.box) for f, v in stops]) if (verify and stops) else [{"static": False}] * len(stops)
    confirmed = {id(v) for (_, v), r in zip(stops, verdicts) if r.get("static")}

    # One identity per stopped spot: same view, box centres within 80/1000 of each other.
    spots: list[tuple[int | None, float, float, str]] = []
    persistent: dict[tuple, list[float]] = {}

    def spot_label(v) -> str:
        cx, cy = (v.box[0] + v.box[2]) / 2, (v.box[1] + v.box[3]) / 2
        for view, x, y, label in spots:
            if view == v.view and abs(x - cx) <= 80 and abs(y - cy) <= 80:
                return label
        spots.append((v.view, cx, cy, v.label))
        return v.label

    hazard_seen: dict[tuple, int] = {}
    for _, a in timed:
        for h in a.hazards:
            hazard_seen[(h.view, h.type)] = hazard_seen.get((h.view, h.type), 0) + 1

    out, dropped = [], {"stopped": 0, "transient": 0}
    for f, a in timed:
        keep_v = []
        for v in a.vehicles:
            if v.action == "stopped":
                if id(v) in confirmed:
                    v = v.model_copy(update={"label": spot_label(v)})
                    persistent.setdefault((v.view, _key(v.label)), []).append(f.t_s)
                    keep_v.append(v)
                else:
                    dropped["stopped"] += 1
            elif v.action in TRANSIENT_ACTIONS:
                dropped["transient"] += 1
            else:
                keep_v.append(v)
        keep_h = []
        for h in a.hazards:
            if h.type == "stopped_in_lane":
                continue  # represented by the verified stopped vehicle itself
            if h.type in TRANSIENT_HAZARDS:
                dropped["transient"] += 1
            elif hazard_seen[(h.view, h.type)] >= 2:
                keep_h.append(h)
        out.append((f, a.model_copy(update={"vehicles": keep_v, "hazards": keep_h})))
    summary = {k: (len(ts), min(ts), max(ts)) for k, ts in persistent.items()}
    return out, summary, dropped


def _key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def frames_to_observations(
    evidence: Evidence, info: VideoInfo, analysed: list[tuple[Frame, FrameAnalysis]], mode: CameraMode = "dashcam",
    persistence: dict | None = None,
) -> list[Observation]:
    """Collapse per-frame findings into state changes; one Observation per change.

    Fixed mode: no ego vehicle (an "ego" answer is ignored), signals tied to an "ego lane" are
    dropped, plain "moving" traffic is not reported, and every observation carries its camera view.
    """
    fixed = mode == "fixed"
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
            timestamp=config.INCIDENT_REFERENCE_TIME + timedelta(seconds=t_rel),
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

    def with_view(attrs: dict, view) -> dict:
        if fixed:
            attrs["camera"] = "fixed"
            if view:
                attrs["view"] = view
        return attrs

    for frame, a in sorted(analysed, key=lambda fa: fa[0].index):
        # Fixed cameras: a signal only counts if the model confirmed it can see a light head.
        for s in (a.signals if not fixed or a.signal_heads_visible else []):
            if s.state == "unknown" or s.confidence < MIN_CONFIDENCE:
                continue
            if fixed and "ego" in _key(s.approach):
                continue  # no ego lane on a fixed camera: a dashcam-style guess
            if changed(("signal", _key(s.approach), str(s.view if fixed else "")), s.state):
                emit(frame, "signal_state", s.confidence,
                     attributes=with_view({"approach": s.approach or "unknown", "signal_state": s.state}, s.view))

        if not fixed and a.ego and a.ego.confidence >= MIN_CONFIDENCE and changed(("vehicle", "ego"), a.ego.action):
            attrs = {"ego": True}
            if a.ego.cue:
                attrs["cue"] = a.ego.cue
            emit(frame, a.ego.action, a.ego.confidence, vehicle_id=EGO_VEHICLE_ID,
                 description="ego vehicle (dashcam host)", attributes=attrs)

        for v in a.vehicles:
            if v.confidence < MIN_CONFIDENCE or not _key(v.label):
                continue
            key = ("vehicle", _key(v.label), str(v.view if fixed else ""))
            if fixed and v.action == "moving":
                last_state[key] = "moving"  # flowing traffic is not evidence, but a later change is
                continue
            if changed(key, v.action):
                attrs = {"label": v.label}
                if v.direction:
                    attrs["direction"] = v.direction
                if fixed and v.box:
                    attrs["box"] = v.box  # 0-1000 over the whole frame; lets the reconstruction place it
                    attrs["frame_size"] = [info.width, info.height]
                if (p := (persistence or {}).get((v.view, _key(v.label)))) and v.action == "stopped":
                    attrs.update(seen_in_keyframes=p[0], first_seen_s=round(p[1], 2), last_seen_s=round(p[2], 2))
                emit(frame, v.action, v.confidence, description=v.description or v.label,
                     attributes=with_view(attrs, v.view))

        for h in a.hazards if fixed else []:
            if h.confidence < MIN_CONFIDENCE:
                continue
            if changed(("hazard", h.type, str(h.view or "")), "present"):
                emit(frame, "hazard", h.confidence, description=h.description,
                     attributes=with_view({"hazard": h.type}, h.view))
        if fixed:  # a hazard that disappears can be reported again if it comes back
            seen = {("hazard", h.type, str(h.view or "")) for h in a.hazards if h.confidence >= MIN_CONFIDENCE}
            for k in [k for k in last_state if k[0] == "hazard" and k not in seen]:
                del last_state[k]

        c = a.collision
        if c and c.visible and c.confidence >= MIN_CONFIDENCE and not collision_seen:
            collision_seen = True
            vehicles = c.vehicles if fixed else [
                EGO_VEHICLE_ID if _key(x) in {"ego", "ego vehicle", "dashcam"} else x for x in c.vehicles]
            attrs = {"vehicles": vehicles}
            if c.impact:
                attrs["impact_side"] = c.impact
            emit(frame, "collision", c.confidence, attributes=with_view(attrs, c.view))
    return out
