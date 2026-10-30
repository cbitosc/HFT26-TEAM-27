"""Trajectory reconstruction on the predefined four-way intersection.

1. Keyframes: every observation with a time and numeric x_m/y_m is a measured
   point of its vehicle (one per instant; the most confident one wins).
2. Tracks: keyframes are resampled to a 0.5 s grid. Between two keyframes whose
   speed and heading agree with the displacement we use cubic Hermite
   interpolation, which is exact for constant velocity and constant braking
   (so the V1 sensor gap is bridged at constant velocity). Elsewhere, e.g.
   across the collision impulse, we interpolate in a straight line. Outside the
   keyframes a vehicle continues at constant velocity, or holds if stopped.
3. Vehicles that are mentioned but never positioned get an approach-direction
   default: constant speed along their lane, reaching the conflict point at
   the collision (or their entering_intersection) time.
4. The collision marker is emitted only if the timeline has a collision event
   backed by known observations. It sits at the vehicles' closest approach.

The measured keyframes are listed in VehicleTrack.source_observation_ids, so a
viewer can tell measured track points from interpolated ones.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Callable

from app.schemas import (
    CollisionMarker,
    ConflictReport,
    Observation,
    ObservationSet,
    Reconstruction,
    Scene,
    SourceType,
    Timeline,
    TimelineEvent,
    TrackPoint,
    VehicleTrack,
)

EXTENT_M = 60.0
LANE_W = 3.5
ROADS = {"ns": "Main Rd", "ew": "5th Ave"}
STEP_S = 0.5
KMH = 3.6
DEFAULT_SPEED_KMH = 40.0

# Left-hand traffic: approach -> (heading_deg, fixed lane coordinate, axis it travels along).
APPROACHES: dict[str, tuple[float, float, str]] = {
    "NB": (0.0, -LANE_W / 2, "y"),
    "SB": (180.0, LANE_W / 2, "y"),
    "EB": (90.0, LANE_W / 2, "x"),
    "WB": (270.0, -LANE_W / 2, "x"),
}
DIRECTION_WORDS = {"northbound": "NB", "southbound": "SB", "eastbound": "EB", "westbound": "WB",
                   "north": "NB", "south": "SB", "east": "EB", "west": "WB"}
COLORS = {"red": "#dc2626", "white": "#64748b", "black": "#0f172a", "blue": "#2563eb", "silver": "#94a3b8",
          "grey": "#94a3b8", "gray": "#94a3b8", "green": "#16a34a", "yellow": "#ca8a04", "orange": "#ea580c"}
PALETTE = ["#7c3aed", "#0891b2", "#db2777", "#65a30d"]

# (x, y, heading_deg, speed m/s)
State = tuple[float, float, float, float]
Sampler = Callable[[float], State]


@dataclass
class Keyframe:
    t: float
    x: float
    y: float
    heading: float | None
    speed: float | None  # m/s
    obs_id: str
    confidence: float

    def velocity(self) -> tuple[float, float] | None:
        if self.speed is None:
            return None
        if self.speed == 0:
            return (0.0, 0.0)
        if self.heading is None:
            return None
        h = math.radians(self.heading)
        return (self.speed * math.sin(h), self.speed * math.cos(h))


# ------------------------------------------------------------------ helpers

def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _t_rel(o: Observation, ref: datetime) -> float | None:
    if o.t_rel_s is not None:
        return o.t_rel_s
    if o.timestamp is not None:
        ts = o.timestamp if o.timestamp.tzinfo else o.timestamp.replace(tzinfo=ref.tzinfo)
        return (ts - ref).total_seconds()
    return None


def _speed_ms(o: Observation) -> float | None:
    a = o.attributes
    if (v := _num(a.get("speed_kmh"))) is not None:
        return v / KMH
    if (v := _num(a.get("speed_mph"))) is not None:
        return v * 1.609344 / KMH
    if o.action == "stopped":
        return 0.0
    return None


def _norm(h: float) -> float:
    return h % 360.0


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _lerp_angle(a: float, b: float, k: float) -> float:
    d = (b - a + 180.0) % 360.0 - 180.0
    return _norm(a + d * k)


def _heading_of(vx: float, vy: float) -> float:
    return _norm(math.degrees(math.atan2(vx, vy)))


def _half_floor(t: float) -> float:
    return math.floor(t / STEP_S + 1e-9) * STEP_S


def _half_ceil(t: float) -> float:
    return math.ceil(t / STEP_S - 1e-9) * STEP_S


# ------------------------------------------------------------- keyframes

def _keyframes(observations: list[Observation], ref: datetime) -> dict[str, list[Keyframe]]:
    best: dict[tuple[str, float], Keyframe] = {}
    for o in observations:
        x, y = _num(o.attributes.get("x_m")), _num(o.attributes.get("y_m"))
        t = _t_rel(o, ref)
        if not o.vehicle_id or x is None or y is None or t is None:
            continue
        kf = Keyframe(t, x, y, _num(o.attributes.get("heading_deg")), _speed_ms(o), o.id, o.confidence)
        key = (o.vehicle_id, round(t, 2))
        if key not in best or kf.confidence > best[key].confidence:
            best[key] = kf
    out: dict[str, list[Keyframe]] = {}
    for (vid, _), kf in best.items():
        out.setdefault(vid, []).append(kf)
    for kfs in out.values():
        kfs.sort(key=lambda k: k.t)
    return out


# ---------------------------------------------------------- interpolation

def _hermite_ok(a: Keyframe, b: Keyframe) -> bool:
    """Smooth (along-lane) interpolation only where both ends agree with the displacement."""
    if a.speed is None or b.speed is None or a.heading is None or b.heading is None:
        return False
    dt = b.t - a.t
    dx, dy = b.x - a.x, b.y - a.y
    dist = math.hypot(dx, dy)
    if dist < 0.1 or _angle_diff(a.heading, b.heading) > 5:
        return False
    if _angle_diff(_heading_of(dx, dy), a.heading) > 10:
        return False
    mean = dist / dt
    return min(a.speed, b.speed) * 0.8 <= mean <= max(a.speed, b.speed) * 1.2


def _cruise_then_change(a: Keyframe, b: Keyframe) -> Callable[[float], tuple[float, float]] | None:
    """Straight-line profile: hold a's speed, then change speed uniformly to b's.

    This is how a driver brakes (or accelerates) and is exact for the scenario's
    motion. Returns tau -> (distance along a->b, speed), or None if no such
    profile covers the measured distance.
    """
    dt, va, vb = b.t - a.t, a.speed, b.speed
    dist = math.hypot(b.x - a.x, b.y - a.y)
    if abs(va - vb) < 1e-6:
        if abs(va * dt - dist) > 0.05 * dist + 0.1:
            return None
        v = dist / dt
        return lambda tau: (v * tau, v)
    t_change = 2 * (va * dt - dist) / (va - vb)
    if not -1e-6 <= t_change <= dt + 1e-6:
        return None
    t1, acc = dt - t_change, (vb - va) / max(t_change, 1e-9)

    def at(tau: float) -> tuple[float, float]:
        if tau <= t1:
            return va * tau, va
        return va * tau + 0.5 * acc * (tau - t1) ** 2, va + acc * (tau - t1)

    return at


def _segment(a: Keyframe, b: Keyframe, t: float) -> State:
    dt = b.t - a.t
    s = (t - a.t) / dt
    profile = _cruise_then_change(a, b) if _hermite_ok(a, b) else None
    if profile is not None:
        along, speed = profile(t - a.t)
        dist = math.hypot(b.x - a.x, b.y - a.y)
        k = along / dist
        return a.x + (b.x - a.x) * k, a.y + (b.y - a.y) * k, _lerp_angle(a.heading, b.heading, s), speed
    if _hermite_ok(a, b):
        (ax, ay), (bx, by) = a.velocity(), b.velocity()
        h00, h10, h01, h11 = 2*s**3 - 3*s**2 + 1, s**3 - 2*s**2 + s, -2*s**3 + 3*s**2, s**3 - s**2
        d00, d10, d01, d11 = 6*s**2 - 6*s, 3*s**2 - 4*s + 1, -6*s**2 + 6*s, 3*s**2 - 2*s
        x = h00*a.x + h10*dt*ax + h01*b.x + h11*dt*bx
        y = h00*a.y + h10*dt*ay + h01*b.y + h11*dt*by
        vx = (d00*a.x + d10*dt*ax + d01*b.x + d11*dt*bx) / dt
        vy = (d00*a.y + d10*dt*ay + d01*b.y + d11*dt*by) / dt
        speed = math.hypot(vx, vy)
        heading = _heading_of(vx, vy) if speed > 0.3 else _lerp_angle(a.heading, b.heading, s)
        return x, y, heading, speed

    x, y = a.x + (b.x - a.x) * s, a.y + (b.y - a.y) * s
    disp_heading = _heading_of(b.x - a.x, b.y - a.y) if math.hypot(b.x - a.x, b.y - a.y) > 0.1 else None
    ha, hb = a.heading if a.heading is not None else disp_heading, b.heading if b.heading is not None else disp_heading
    if ha is not None and hb is not None:
        heading = _lerp_angle(ha, hb, s)
    else:
        heading = ha if ha is not None else (hb if hb is not None else 0.0)
    if a.speed is not None and b.speed is not None:
        speed = a.speed + (b.speed - a.speed) * s
    else:
        speed = math.hypot(b.x - a.x, b.y - a.y) / dt
    return x, y, heading, speed


def _edge_velocity(k: Keyframe, neighbour: Keyframe | None) -> tuple[float, float]:
    """Velocity to extrapolate with beyond the first/last keyframe."""
    if (v := k.velocity()) is not None:
        return v
    if neighbour is not None and neighbour.t != k.t:
        dt = k.t - neighbour.t
        return ((k.x - neighbour.x) / dt, (k.y - neighbour.y) / dt)
    return (0.0, 0.0)


def _keyframe_sampler(kfs: list[Keyframe]) -> Sampler:
    first, last = kfs[0], kfs[-1]
    v_first = _edge_velocity(first, kfs[1] if len(kfs) > 1 else None)
    v_last = _edge_velocity(last, kfs[-2] if len(kfs) > 1 else None)

    def extrapolate(k: Keyframe, v: tuple[float, float], t: float) -> State:
        speed = math.hypot(*v)
        heading = k.heading if k.heading is not None else (_heading_of(*v) if speed > 0 else 0.0)
        return k.x + v[0] * (t - k.t), k.y + v[1] * (t - k.t), heading, speed

    def sample(t: float) -> State:
        if t <= first.t:
            return extrapolate(first, v_first, t)
        if t >= last.t:
            return extrapolate(last, v_last, t)
        for a, b in zip(kfs, kfs[1:]):
            if a.t <= t <= b.t:
                return _segment(a, b, t)
        raise AssertionError("unreachable")

    return sample


def _lane_sampler(approach: str, conflict: tuple[float, float], t_arr: float, speed: float, hold_after: bool) -> Sampler:
    heading, lane, axis = APPROACHES[approach]
    sign = 1.0 if approach in ("NB", "EB") else -1.0
    along0 = conflict[1] if axis == "y" else conflict[0]

    def sample(t: float) -> State:
        if hold_after and t > t_arr:
            t, v = t_arr, 0.0
        else:
            v = speed
        along = along0 + sign * speed * (t - t_arr)
        x, y = (lane, along) if axis == "y" else (along, lane)
        return x, y, heading, v

    return sample


# ------------------------------------------------------------ descriptions

def _approach_of(obs: list[Observation]) -> str | None:
    for o in sorted(obs, key=lambda o: -o.confidence):
        a = o.attributes
        if isinstance(a.get("approach"), str) and a["approach"].upper() in APPROACHES:
            return a["approach"].upper()
        if isinstance(a.get("direction"), str) and a["direction"].lower() in DIRECTION_WORDS:
            return DIRECTION_WORDS[a["direction"].lower()]
        if (h := _num(a.get("heading_deg"))) is not None:
            return min(APPROACHES, key=lambda k: _angle_diff(APPROACHES[k][0], h))
    return None


def _description(obs: list[Observation]) -> str | None:
    """Prefer a description that names a colour (sensor ones are often just a unit id)."""
    rank = {SourceType.sensor: 0, SourceType.video: 1, SourceType.witness: 2}
    has_colour = lambda o: any(w in COLORS for w in (o.vehicle_description or "").lower().replace(",", " ").split())  # noqa: E731
    for o in sorted(obs, key=lambda o: (not has_colour(o), rank[o.source_type], -o.confidence)):
        if o.vehicle_description:
            return o.vehicle_description
    return None


def _color(desc: str | None, index: int) -> str:
    for word in (desc or "").lower().replace(",", " ").split():
        if word in COLORS:
            return COLORS[word]
    return PALETTE[index % len(PALETTE)]


# --------------------------------------------------------------- collision

def _collision_event(timeline: Timeline, known: set[str]) -> tuple[TimelineEvent, list[str]] | None:
    """Most confident collision event that is backed by observations we have."""
    best = None
    for e in timeline.events:
        if e.category != "collision":
            continue
        support = [i for i in e.observation_ids if i in known]
        if support and (best is None or e.confidence > best[0].confidence):
            best = (e, support)
    return best


def _collision_vehicles(event: TimelineEvent, support: list[str], by_id: dict[str, Observation]) -> list[str]:
    vids = list(event.vehicle_ids)
    for i in support:
        for v in by_id[i].attributes.get("vehicles") or []:
            if isinstance(v, str) and v not in vids:
                vids.append(v)
    return vids


def _closest_approach(event: TimelineEvent, samplers: dict[str, Sampler], vids: list[str]):
    """(t, x, y, distance) where the involved vehicles are closest around the event."""
    vids = [v for v in vids if v in samplers]
    t_mid = (event.t_start_s + event.t_end_s) / 2
    if not vids:
        return None
    if len(vids) == 1:
        x, y, _, _ = samplers[vids[0]](t_mid)
        return t_mid, x, y, 0.0
    pad = max(event.uncertainty_s, 0.5)
    lo, hi = event.t_start_s - pad, event.t_end_s + pad
    times = {round(lo + i * 0.05, 3) for i in range(int(round((hi - lo) / 0.05)) + 1)}
    times |= {event.t_start_s, event.t_end_s}
    best = None
    for t in sorted(times):
        pos = {v: samplers[v](t)[:2] for v in vids}
        for i, a in enumerate(vids):
            for b in vids[i + 1:]:
                d = math.dist(pos[a], pos[b])
                key = (round(d, 4), abs(t - t_mid))
                if best is None or key < best[0]:
                    mx, my = (pos[a][0] + pos[b][0]) / 2, (pos[a][1] + pos[b][1]) / 2
                    best = (key, (t, mx, my, d))
    return best[1]


# -------------------------------------------------------------------- main

def reconstruct(observation_sets: list[ObservationSet], timeline: Timeline,
                conflicts: ConflictReport) -> Reconstruction:
    # `conflicts` is accepted per the contract; the geometry comes from the
    # observations, and contradicting claims (e.g. witness speeds) carry no x/y.
    observations = [o for s in observation_sets for o in s.observations]
    by_id = {o.id: o for o in observations}
    ref = timeline.reference_time

    obs_by_vehicle: dict[str, list[Observation]] = {}
    for o in observations:
        if o.vehicle_id:
            obs_by_vehicle.setdefault(o.vehicle_id, []).append(o)
    vehicle_ids = list(obs_by_vehicle)
    for e in timeline.events:
        vehicle_ids += [v for v in e.vehicle_ids if v not in vehicle_ids]
    vehicle_ids.sort()

    keyframes = _keyframes(observations, ref)
    samplers: dict[str, Sampler] = {vid: _keyframe_sampler(kfs) for vid, kfs in keyframes.items()}
    source_ids: dict[str, list[str]] = {vid: [k.obs_id for k in kfs] for vid, kfs in keyframes.items()}

    collision = _collision_event(timeline, set(by_id))
    coll_vids = _collision_vehicles(*collision, by_id) if collision else []

    # Approach-direction defaults for vehicles we know of but never positioned.
    approaches = {vid: _approach_of(obs_by_vehicle.get(vid, [])) for vid in vehicle_ids}
    for vid in vehicle_ids:
        if vid in samplers or approaches[vid] is None:
            continue
        own = obs_by_vehicle.get(vid, [])
        speeds = [s for o in own if o.source_type != SourceType.witness and (s := _speed_ms(o))]
        speeds = speeds or [s for o in own if (s := _speed_ms(o))]
        speed = median(speeds) if speeds else DEFAULT_SPEED_KMH / KMH
        if collision and vid in coll_vids:
            t_arr, hold = collision[0].t_start_s, True
            conflict = _default_conflict_point(vid, coll_vids, approaches, samplers, t_arr)
        else:
            entering = [t for o in own if o.action == "entering_intersection" and (t := _t_rel(o, ref)) is not None]
            t_arr, hold, conflict = (min(entering) if entering else None), False, (0.0, 0.0)
            if t_arr is None:
                t_arr = collision[0].t_start_s if collision else _anchor_without_collision(keyframes, timeline)
        _, lane, axis = APPROACHES[approaches[vid]]
        conflict = (lane, conflict[1]) if axis == "y" else (conflict[0], lane)
        samplers[vid] = _lane_sampler(approaches[vid], conflict, t_arr, speed, hold)
        source_ids[vid] = [o.id for o in own]

    # Window: ~5 s of approach before the collision and 3 s of aftermath.
    anchor = collision[0].t_start_s if collision else _anchor_without_collision(keyframes, timeline)
    t_min = _half_floor(anchor - 5.0)
    t_max = _half_ceil(anchor + 3.0)
    grid = [round(t_min + i * STEP_S, 3) for i in range(int(round((t_max - t_min) / STEP_S)) + 1)]

    vehicles = []
    for i, vid in enumerate(v for v in vehicle_ids if v in samplers):
        desc = _description(obs_by_vehicle.get(vid, []))
        short = desc.split(",")[0].strip() if desc else None
        track = []
        for t in grid:
            x, y, h, s = samplers[vid](t)
            track.append(TrackPoint(t_s=t, x_m=round(x, 2), y_m=round(y, 2),
                                    heading_deg=round(_norm(h), 1), speed_kmh=round(s * KMH, 1)))
        vehicles.append(VehicleTrack(vehicle_id=vid, label=f"{vid} · {short}" if short else vid,
                                     color=_color(desc, i), description=desc, track=track,
                                     source_observation_ids=source_ids[vid]))

    marker = None
    if collision:
        event, support = collision
        found = _closest_approach(event, samplers, coll_vids)
        if found:
            t, x, y, d = found
            conf = event.confidence * (1.0 if d <= 2.5 else max(0.3, 2.5 / d))
            marker = CollisionMarker(t_s=round(t, 2), x_m=round(x, 2), y_m=round(y, 2),
                                     confidence=round(min(1.0, conf), 2), supporting_observation_ids=support)

    return Reconstruction(incident_id=timeline.incident_id, generated_at=datetime.now(timezone.utc),
                          scene=Scene(extent_m=EXTENT_M, lane_width_m=LANE_W, roads=ROADS),
                          t_min_s=t_min, t_max_s=t_max, vehicles=vehicles, collision=marker)


def _anchor_without_collision(keyframes: dict[str, list[Keyframe]], timeline: Timeline) -> float:
    ends = [kfs[-1].t for kfs in keyframes.values()]
    if not ends:
        ends = [e.t_end_s for e in timeline.events if e.category == "vehicle_motion"]
    return max(ends) if ends else 10.0


def _default_conflict_point(vid: str, coll_vids: list[str], approaches: dict[str, str | None],
                            samplers: dict[str, Sampler], t: float) -> tuple[float, float]:
    """Where an unpositioned vehicle must have met the other party of the collision."""
    for other in coll_vids:
        if other != vid and other in samplers:
            x, y, _, _ = samplers[other](t)
            return x, y
    own_axis = APPROACHES[approaches[vid]][2]
    for other in coll_vids:
        appr = approaches.get(other)
        if other != vid and appr and APPROACHES[appr][2] != own_axis:
            lane = APPROACHES[appr][1]
            return (0.0, lane) if own_axis == "y" else (lane, 0.0)
    return 0.0, 0.0
