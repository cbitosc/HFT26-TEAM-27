"""Module 5 — Reconstruction: where the vehicles were, top-down, in metres.

Two outputs:
* build_reconstruction: the top-down map. From measured positions (sensor / GPS paths) when there are
  any; otherwise from camera tracks of a calibrated view projected onto the road plane; otherwise empty
  with a note saying what evidence is missing. Nothing is drawn without positional evidence.
* build_event_analysis: the camera footage with its detections and image-space tracks (pixels). That is
  evidence playback, not a reconstruction, and the dashboard shows it as "Event Analysis".
"""
from datetime import datetime, timezone

from app.schemas import ConflictReport, ObservationSet, Reconstruction, Scene, Timeline

from .builder import reconstruct
from .camera import reconstruct_from_camera, reconstruct_ground_from_camera


def build_reconstruction(
    observation_sets: list[ObservationSet], timeline: Timeline, conflicts: ConflictReport
) -> Reconstruction:
    metric = reconstruct(observation_sets, timeline, conflicts)
    if metric.vehicles:
        return metric  # measured positions beat projected camera tracks
    ground = reconstruct_ground_from_camera(observation_sets, timeline)
    if ground is not None:
        return ground
    image_tracks = any(o.action == "track" and o.attributes.get("units") == "px"
                       for st in observation_sets for o in st.observations)
    has_video = any(st.source_type.value == "video" for st in observation_sets)
    camera_hint = ("Camera tracks exist but no view is calibrated: declare the lane-marking standard for the case "
                   "or set a manual calibration for a view. " if image_tracks else
                   "The video gives no positions on the road (e.g. a dashcam measures only its own relative motion). "
                   if has_video else "")
    return Reconstruction(
        incident_id=timeline.incident_id, generated_at=datetime.now(timezone.utc),
        scene=Scene(type="metric", extent_m=10), t_min_s=0.0, t_max_s=0.0, vehicles=[], collision=None,
        note="No positional evidence to reconstruct from. " + camera_hint
             + "Telemetry or GPS files with positions give a reconstruction.")


def build_event_analysis(observation_sets: list[ObservationSet], timeline: Timeline) -> Reconstruction | None:
    """Camera footage with detections and tracks in image pixels, or None without video tracks."""
    return reconstruct_from_camera(observation_sets, timeline)


def positions_export(rec: Reconstruction) -> dict:
    """The reconstruction as a plain "id and positions" JSON: one list of timed positions per vehicle.

    Metres (+x east, +y north) for a top-down scene, pixels (+y down) for a camera scene; `basis` says whether
    a position was measured, interpolated, extrapolated, tracked or estimated, so nothing reads as more certain
    than it is.
    """
    px = rec.scene.units == "px"
    return {
        "incident_id": rec.incident_id,
        "generated_at": rec.generated_at.isoformat(),
        "coordinate_system": {
            "units": rec.scene.units,
            "x": "pixels from the left of the camera frame" if px else "metres east of the origin",
            "y": "pixels down from the top of the camera frame" if px else "metres north of the origin",
            "heading_deg": "0 = north, clockwise" if not px else "image-space",
            "time": "seconds from the incident reference time (t_rel_s)",
            "scene": rec.scene.type,
        },
        "t_min_s": rec.t_min_s,
        "t_max_s": rec.t_max_s,
        "vehicles": [
            {"id": v.vehicle_id, "label": v.label, "description": v.description,
             "positions": [{"t_s": p.t_s, "x": p.x_m, "y": p.y_m, "heading_deg": p.heading_deg,
                            "speed_kmh": p.speed_kmh, "basis": p.source, "confidence": p.confidence}
                           for p in v.track],
             "source_observation_ids": v.source_observation_ids}
            for v in rec.vehicles],
        "collision": None if rec.collision is None else {
            "t_s": rec.collision.t_s, "x": rec.collision.x_m, "y": rec.collision.y_m,
            "confidence": rec.collision.confidence,
            "supporting_observation_ids": rec.collision.supporting_observation_ids},
        "note": rec.note,
    }
