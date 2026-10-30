"""Module 2c — Cross-source fusion: events no single evidence file can show.

Each extractor sees one file. Some facts only appear when files are put side by side:
V1's telematics and V2's OBD log are separate CSVs, so neither shows the collision,
but both show a sudden heading change at the same moment and place. Fusion infers
those events as derived observations (source_type sensor, producer "fusion"), each
citing the observations it was derived from, so modules 3-5 can use them like any other.

Rules (all conservative; nothing is inferred without two independent vehicles agreeing):
* collision: two different vehicles with a sudden heading change within PAIR_WINDOW_S of each
  other and within PAIR_DISTANCE_M. The impact is placed one sample interval before the
  earlier change (the change is seen in the first sample after impact).
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


def _xy(o: Observation) -> tuple[float, float] | None:
    x, y = o.attributes.get("x_m"), o.attributes.get("y_m")
    return (float(x), float(y)) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else None


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
    if not derived:
        return None
    return ObservationSet(source_type=SourceType.sensor, evidence_id=EVIDENCE_ID, producer=PRODUCER,
                          generated_at=datetime.now(timezone.utc), observations=derived,
                          warnings=[f"Inferred: {o.action} at t={o.t_rel_s:g} s from {', '.join(o.attributes['inferred_from'])} "
                                    f"({o.attributes['rule']})." for o in derived])
