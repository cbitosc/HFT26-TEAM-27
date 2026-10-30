"""Module 2c — Cross-source fusion: events no single evidence file can show.

Each extractor sees one file. Some facts only appear when files are put side by side:
V1's telematics and V2's OBD log are separate CSVs, so neither shows the collision,
but both show a sudden heading change at the same moment and place. Fusion infers
those events as derived observations (source_type sensor, producer "fusion"), each
citing the observations it was derived from, so modules 3-5 can use them like any other.

Rules (all conservative; nothing is inferred without two independent vehicles agreeing):
* collision by swerve: two different vehicles with a sudden heading change within PAIR_WINDOW_S of
  each other and within PAIR_DISTANCE_M. The impact is placed one sample interval before the
  earlier change (the change is seen in the first sample after impact). Typical of side impacts.
* collision by contact: two measured paths come within CONTACT_M (reference points are ~a vehicle
  length apart when bumpers touch) AND one vehicle's speed drops abruptly or it swerves within
  AFTERMATH_S. Typical of rear-end impacts, which swerve rules miss. Close following alone is not
  enough: without the abrupt change nothing is inferred.

Entity linking (resolve_entities): observations with a description but no vehicle id are linked
to a known vehicle when exactly one matches by declared description, or by a direction stated in
the evidence matching exactly one measured vehicle's heading. Descriptions that match nothing get
a shared provisional id (e.g. U-white-suv), so a witness's and the video's "white SUV" can be
compared. Every link is recorded in attributes.vehicle_link with its method.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import combinations
from math import hypot

from app import config
from app.schemas import Observation, ObservationSet, SourceRef, SourceType

PRODUCER = "fusion"
EVIDENCE_ID = "fusion"
PAIR_WINDOW_S = 1.0
PAIR_DISTANCE_M = 6.0
CONTACT_M = 5.0
AFTERMATH_S = 1.5
ABRUPT_DROP_KMH = 10.0   # speed drop within 1 s that counts as an impact
STEP_S = 0.1


def _xy(o: Observation) -> tuple[float, float] | None:
    x, y = o.attributes.get("x_m"), o.attributes.get("y_m")
    return (float(x), float(y)) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else None


def _paths(observations: list[Observation]) -> dict[str, tuple[Observation, list[list[float]]]]:
    return {o.vehicle_id: (o, o.attributes["points"]) for o in observations
            if o.action == "track" and o.attributes.get("units") == "m" and o.vehicle_id
            and len(o.attributes.get("points", [])) >= 2}


def _at(points: list[list[float]], t: float):
    """(x, y) interpolated at t, or None outside the path."""
    if t < points[0][0] or t > points[-1][0]:
        return None
    for a, b in zip(points, points[1:]):
        if a[0] <= t <= b[0]:
            k = 0.0 if b[0] == a[0] else (t - a[0]) / (b[0] - a[0])
            return a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k
    return None


def _abrupt_change(points: list[list[float]], t: float) -> str | None:
    """Impact signature on one path right after t: a sharp speed drop or swerve."""
    samples = [p for p in points if t - 0.2 <= p[0] <= t + AFTERMATH_S]
    for a in samples:
        for b in samples:
            if 0 < b[0] - a[0] <= 1.0:
                if len(a) > 3 and a[3] is not None and b[3] is not None and a[3] - b[3] >= ABRUPT_DROP_KMH:
                    return f"a speed drop of {a[3] - b[3]:.0f} km/h within {b[0] - a[0]:.1f} s"
                if len(a) > 4 and a[4] is not None and b[4] is not None and (a[3] or 0) > 5:
                    turn = abs((b[4] - a[4] + 180) % 360 - 180)
                    if turn >= 15:
                        return f"a {turn:.0f} deg swerve within {b[0] - a[0]:.1f} s"
    return None


def infer_contact_collisions(observations: list[Observation]) -> list[Observation]:
    paths = _paths(observations)
    found = []
    for (va, (oa, pa)), (vb, (ob, pb)) in combinations(sorted(paths.items()), 2):
        lo, hi = max(pa[0][0], pb[0][0]), min(pa[-1][0], pb[-1][0])
        t = lo
        while t <= hi:
            a, b = _at(pa, t), _at(pb, t)
            if a and b and hypot(a[0] - b[0], a[1] - b[1]) <= CONTACT_M:
                why = _abrupt_change(pa, t) or _abrupt_change(pb, t)
                if why:
                    found.append((t, va, vb, oa, ob, hypot(a[0] - b[0], a[1] - b[1]), why, a, b))
                break  # first contact decides; later closeness is the aftermath
            t = round(t + STEP_S, 3)
    out = []
    for n, (t, va, vb, oa, ob, dist, why, a, b) in enumerate(sorted(found, key=lambda f: f[0]), start=1):
        out.append(Observation(
            id=f"fus_contact_{n:03d}", source_type=SourceType.sensor,
            source_ref=SourceRef(evidence_id=EVIDENCE_ID, file="(derived)", locator=f"from:{oa.id},{ob.id}"),
            timestamp=config.INCIDENT_REFERENCE_TIME + timedelta(seconds=t), t_rel_s=round(t, 2),
            time_uncertainty_s=STEP_S * 2, vehicle_id=None, action="collision", basis="derived",
            derived_from=[oa.id, ob.id],
            attributes={"vehicles": [va, vb], "inferred_from": [oa.id, ob.id],
                        "rule": f"measured paths within {CONTACT_M:g} m and {why}", "separation_m": round(dist, 2),
                        "x_m": round((a[0] + b[0]) / 2, 2), "y_m": round((a[1] + b[1]) / 2, 2)},
            confidence=round(min(oa.confidence, ob.confidence) * 0.85, 2)))
    return out


def infer_collisions(observations: list[Observation]) -> list[Observation]:
    if any(o.action == "collision" and o.source_type != SourceType.witness for o in observations):
        return []  # a camera or sensor already saw it; nothing to infer
    changes = [o for o in observations if o.action == "sudden_heading_change" and o.vehicle_id
               and o.t_rel_s is not None and _xy(o) is not None]
    pairs = []
    for a, b in combinations(changes, 2):
        if a.vehicle_id == b.vehicle_id or abs(a.t_rel_s - b.t_rel_s) > PAIR_WINDOW_S:
            continue
        (ax, ay), (bx, by) = _xy(a), _xy(b)
        dist = hypot(ax - bx, ay - by)
        if dist <= PAIR_DISTANCE_M:
            pairs.append((abs(a.t_rel_s - b.t_rel_s) + dist / 10, a, b, dist))
    if not pairs:
        return []
    _, a, b, dist = min(pairs, key=lambda p: p[0])  # one incident, best-matching pair
    step = max(a.time_uncertainty_s, b.time_uncertainty_s)  # one sample interval
    t = round(min(a.t_rel_s, b.t_rel_s) - step, 3)
    (ax, ay), (bx, by) = _xy(a), _xy(b)
    return [Observation(
        id="fus_collision_001",
        source_type=SourceType.sensor,
        source_ref=SourceRef(evidence_id=EVIDENCE_ID, file="(derived)", locator=f"from:{a.id},{b.id}"),
        timestamp=config.INCIDENT_REFERENCE_TIME + timedelta(seconds=t),
        t_rel_s=t,
        time_uncertainty_s=round(step, 3),
        vehicle_id=None,
        action="collision",
        basis="derived",
        derived_from=[a.id, b.id],
        attributes={"vehicles": sorted({a.vehicle_id, b.vehicle_id}), "inferred_from": [a.id, b.id],
                    "rule": f"sudden heading changes of two vehicles within {PAIR_WINDOW_S:g} s and "
                            f"{PAIR_DISTANCE_M:g} m", "separation_m": round(dist, 2),
                    "x_m": round((ax + bx) / 2, 2), "y_m": round((ay + by) / 2, 2)},
        confidence=round(min(a.confidence, b.confidence) * 0.9, 2),
    )]


def fuse(observation_sets: list[ObservationSet]) -> ObservationSet | None:
    """Derived observations across all sets, or None when there is nothing to add."""
    observations = [o for s in observation_sets for o in s.observations]
    derived = infer_collisions(observations)
    if not derived and not any(o.action == "collision" and o.source_type != SourceType.witness for o in observations):
        derived = infer_contact_collisions(observations)
    if not derived:
        return None
    return ObservationSet(source_type=SourceType.sensor, evidence_id=EVIDENCE_ID, producer=PRODUCER,
                          generated_at=datetime.now(timezone.utc), observations=derived,
                          warnings=[f"Inferred: {o.action} at t={o.t_rel_s:g} s from {', '.join(o.attributes['inferred_from'])} "
                                    f"({o.attributes['rule']})." for o in derived])


# ------------------------------------------------------------------ entity linking

_APPROACH_OF_HEADING = [(45, "NB"), (135, "EB"), (225, "SB"), (315, "WB"), (360, "NB")]
_DIRECTION_TO_APPROACH = {"northbound": "NB", "southbound": "SB", "eastbound": "EB", "westbound": "WB"}


def _approach_of_heading(h: float) -> str:
    h = h % 360
    return next(code for limit, code in _APPROACH_OF_HEADING if h < limit)


def _measured_approaches(observations: list[Observation]) -> dict[str, str]:
    """Each measured vehicle's direction of travel (from its path headings, before any impact)."""
    out = {}
    for vid, (_, pts) in _paths(observations).items():
        hs = [p[4] for p in pts[: max(2, len(pts) // 2)] if len(p) > 4 and p[4] is not None]
        if hs:
            out[vid] = _approach_of_heading(sorted(hs)[len(hs) // 2])
    return out


def resolve_entities(observation_sets: list[ObservationSet]) -> tuple[list[ObservationSet], list[str]]:
    """Give described-but-unnamed vehicles an id: declared match, unique direction match, or a shared
    provisional id for the same colour + type. Returns new sets and one note per kind of link."""
    from app.modules.extraction.witness import _words, resolve_vehicle

    observations = [o for s in observation_sets for o in s.observations]
    approaches = _measured_approaches(observations)
    counts: dict[str, int] = {}
    out = []
    for s in observation_sets:
        new = []
        for o in s.observations:
            if o.vehicle_id or not o.vehicle_description or o.action in ("track", "collision"):
                new.append(o)
                continue
            vid, method, kind = resolve_vehicle(o.vehicle_description), "declared description", "declared description"
            appr = o.attributes.get("approach") or _DIRECTION_TO_APPROACH.get(str(o.attributes.get("direction", "")).lower())
            if not vid and appr:
                same = [v for v, a in approaches.items() if a == appr]
                if len(same) == 1:
                    vid, method, kind = same[0], f"stated direction {appr} matches its measured heading", "direction"
            if not vid:
                colours, types = _words(o.vehicle_description)
                types -= {"car", "vehicle"} if len(types) > 1 else set()
                if colours and types:
                    vid, method, kind = ("U-" + "-".join(sorted(colours) + sorted(types)),
                                         "provisional: same colour and type", "shared provisional id")
            if vid:
                counts[kind] = counts.get(kind, 0) + 1
                o = o.model_copy(update={"vehicle_id": vid, "attributes": {
                    **o.attributes, "vehicle_link": {"method": method, "basis": "derived"}}})
            new.append(o)
        out.append(s.model_copy(update={"observations": new}))
    notes = [f"{n} observation(s) linked to a vehicle by {m}" for m, n in counts.items()]
    return out, notes
