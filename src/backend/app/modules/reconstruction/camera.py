"""Reconstruction on the camera image, for fixed-camera video without metric positions.

Uses the motion tracks from module 1 (action "track", pixel coordinates) and places vision-verified
stopped vehicles at their box centres. Coordinates stay in image pixels: turning them into metres
would need a camera calibration (a homography per view), which the evidence does not provide.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone

from app.schemas import CameraImage, Observation, ObservationSet, Reconstruction, Scene, Timeline, TrackPoint, VehicleTrack

VIEW_COLORS = ["#0ea5e9", "#22c55e", "#f59e0b", "#a855f7", "#14b8a6", "#ec4899"]
STOPPED_COLOR = "#dc2626"


def _heading(dx: float, dy: float) -> float:
    """Direction of motion on the image, degrees clockwise from image-up."""
    return round(math.degrees(math.atan2(dx, -dy)) % 360, 1)


def _offset(tracks: list[Observation]) -> float:
    """t_rel_s - frame / fps for the tracks (the camera clock offset), so a time maps back to a frame."""
    for o in tracks:
        fps = o.attributes.get("fps")
        loc = o.source_ref.locator
        if fps and loc.startswith("frames:"):
            first = int(loc.split(":")[1].split("-")[0])
            return round(o.attributes["points"][0][0] - first / float(fps), 3)
    return 0.0


def reconstruct_from_camera(observation_sets: list[ObservationSet], timeline: Timeline) -> Reconstruction | None:
    observations = [o for s in observation_sets for o in s.observations]
    tracks = [o for o in observations if o.action == "track" and o.attributes.get("units") == "px"]
    stopped = [o for o in observations if o.action == "stopped" and o.attributes.get("box")
               and o.attributes.get("camera") == "fixed"]
    if not tracks and not stopped:
        return None
    # One video per scene: the one with the most tracks.
    evidence_id = Counter(o.source_ref.evidence_id for o in tracks + stopped).most_common(1)[0][0]
    tracks = [o for o in tracks if o.source_ref.evidence_id == evidence_id]
    stopped = [o for o in stopped if o.source_ref.evidence_id == evidence_id]
    ref = (tracks or stopped)[0].attributes
    width, height = ref.get("frame_size", [0, 0])
    fps = float(next((o.attributes["fps"] for o in tracks if o.attributes.get("fps")), 0) or 0)
    views = next((o.attributes["grid"] for o in tracks if o.attributes.get("grid")), None) or sorted(
        {tuple(o.attributes["view_box"]) for o in tracks if o.attributes.get("view_box")}, key=lambda b: (b[1], b[0]))

    vehicles: list[VehicleTrack] = []
    for o in tracks:
        pts = o.attributes["points"]
        track = []
        for k, (t, x, y) in enumerate(pts):
            a, b = pts[max(0, k - 1)], pts[min(len(pts) - 1, k + 1)]
            track.append(TrackPoint(t_s=t, x_m=x, y_m=y, heading_deg=_heading(b[1] - a[1], b[2] - a[2])))
        view = o.attributes["view"]
        vehicles.append(VehicleTrack(
            vehicle_id=o.attributes["track_id"], label=f"{o.attributes['track_id']} · view {view}",
            color=VIEW_COLORS[(view - 1) % len(VIEW_COLORS)],
            description=f"moving, {o.attributes.get('direction', '')}, ~{o.attributes.get('mean_speed_px_s', '?')} px/s",
            track=track, source_observation_ids=[o.id]))

    for n, o in enumerate(stopped, start=1):
        x1, y1, x2, y2 = o.attributes["box"]
        w, h = o.attributes.get("frame_size", [width, height])
        cx, cy = round((x1 + x2) / 2000 * w, 1), round((y1 + y2) / 2000 * h, 1)
        t0 = o.attributes.get("first_seen_s", o.t_rel_s or 0.0)
        t1 = max(o.attributes.get("last_seen_s", t0), t0)
        view = o.attributes.get("view")
        vehicles.append(VehicleTrack(
            vehicle_id=f"S{view or 0}-{n}", label=f"{o.attributes.get('label', 'vehicle')} (stopped, view {view})",
            color=STOPPED_COLOR, description=o.vehicle_description,
            track=[TrackPoint(t_s=t0, x_m=cx, y_m=cy, heading_deg=0), TrackPoint(t_s=t1, x_m=cx, y_m=cy, heading_deg=0)],
            source_observation_ids=[o.id]))

    times = [p.t_s for v in vehicles for p in v.track]
    t_min, t_max = min(times), max(times)
    background = round(((t_min + t_max) / 2) * fps) if fps else 0
    if stopped:  # show the frame where the verified stop was first confirmed
        background = int(stopped[0].source_ref.locator.split(":")[1]) if stopped[0].source_ref.locator.startswith("frame:") else background
    return Reconstruction(
        incident_id=timeline.incident_id, generated_at=datetime.now(timezone.utc),
        scene=Scene(type="camera_image", units="px",
                    image=CameraImage(evidence_id=evidence_id, frame=background, width=int(width), height=int(height),
                                      views=[list(v) for v in views], fps=fps or None, t_offset_s=_offset(tracks))),
        t_min_s=round(t_min, 3), t_max_s=round(t_max, 3), vehicles=vehicles, collision=None)
