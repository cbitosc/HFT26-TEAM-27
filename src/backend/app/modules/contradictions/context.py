"""Indexes over the observations and timeline that every rule needs.

Witness observations rarely carry a t_rel_s of their own. Their time is taken
from the timeline event that groups them (module 3 placed them there), or, for
claims about a vehicle's approach, from that vehicle's sensor-anchored entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas import Observation, ObservationSet, SourceType, Timeline, TimelineEvent

# Heading (deg, 0 = north, clockwise) -> signal approach the vehicle uses.
_APPROACH_BY_HEADING = {0: "NB", 90: "EB", 180: "SB", 270: "WB"}
APPROACH_NAMES = {"NB": "Northbound", "EB": "Eastbound", "SB": "Southbound", "WB": "Westbound"}
# Authority when sources disagree on the same fact.
SOURCE_RANK = {SourceType.sensor: 0, SourceType.video: 1, SourceType.witness: 2}


def witness_label(o: Observation) -> str:
    """'W1' from ids like wit_w1_03; falls back to the file name."""
    parts = o.id.split("_")
    if len(parts) >= 2 and parts[1].lower().startswith("w") and parts[1][1:].isdigit():
        return parts[1].upper()
    return o.source_ref.file


def source_label(o: Observation) -> str:
    if o.source_type == SourceType.witness:
        return witness_label(o)
    if o.source_type == SourceType.video:
        return "dashcam"
    if o.action == "signal_state" and o.vehicle_id is None:
        return "signal controller"
    return f"{o.vehicle_id} telemetry" if o.vehicle_id else "sensor"


@dataclass
class Context:
    observations: list[Observation]
    timeline: Timeline
    by_id: dict[str, Observation] = field(default_factory=dict)
    events_by_obs: dict[str, list[TimelineEvent]] = field(default_factory=dict)

    @classmethod
    def build(cls, observation_sets: list[ObservationSet], timeline: Timeline) -> "Context":
        obs: list[Observation] = []
        seen: set[str] = set()
        for s in observation_sets:
            for o in s.observations:
                if o.action == "track":
                    continue  # raw motion tracks carry no claims to compare
                if o.id not in seen:
                    seen.add(o.id)
                    obs.append(o)
        ctx = cls(observations=obs, timeline=timeline)
        ctx.by_id = {o.id: o for o in obs}
        for e in timeline.events:
            for oid in e.observation_ids:
                ctx.events_by_obs.setdefault(oid, []).append(e)
        return ctx

    # ------------------------------------------------------------ time

    def t_of(self, o: Observation) -> float | None:
        """Best point time for an observation: its own, else the absolute timestamp."""
        if o.t_rel_s is not None:
            return o.t_rel_s
        if o.timestamp is not None and o.source_type != SourceType.witness:
            return (o.timestamp - self.timeline.reference_time).total_seconds()
        return None

    def window_of(self, o: Observation) -> tuple[float, float] | None:
        """Time window an observation refers to, from itself or its timeline event."""
        t = self.t_of(o)
        if t is not None:
            return (t - o.time_uncertainty_s, t + o.time_uncertainty_s)
        events = self.events_by_obs.get(o.id)
        if events:
            # Narrowest event is the most specific placement.
            e = min(events, key=lambda e: e.t_end_s - e.t_start_s)
            return (e.t_start_s - e.uncertainty_s, e.t_end_s + e.uncertainty_s)
        return None

    def collision_t(self) -> float | None:
        for e in self.timeline.events:
            if e.category == "collision":
                return e.t_start_s
        for o in self.timed(lambda o: o.action == "collision"):
            return self.t_of(o)
        return None

    # ------------------------------------------------------------ lookups

    def timed(self, pred) -> list[Observation]:
        """Observations with a usable time matching pred, sorted by time then authority."""
        out = [o for o in self.observations if pred(o) and self.t_of(o) is not None]
        return sorted(out, key=lambda o: (self.t_of(o), SOURCE_RANK[o.source_type]))

    def speed_samples(self, vehicle_id: str) -> list[Observation]:
        return self.timed(lambda o: o.source_type == SourceType.sensor and o.vehicle_id == vehicle_id
                          and isinstance(o.attributes.get("speed_kmh"), (int, float)))

    def entry_t(self, vehicle_id: str) -> tuple[float, Observation | None] | None:
        """When the vehicle entered the junction, preferring sensor over video."""
        entries = self.timed(lambda o: o.vehicle_id == vehicle_id and o.action == "entering_intersection"
                             and o.source_type != SourceType.witness)
        if entries:
            best = min(entries, key=lambda o: SOURCE_RANK[o.source_type])
            return self.t_of(best), best
        for e in self.timeline.events:
            if e.category == "collision" and vehicle_id in e.vehicle_ids:
                return e.t_start_s, None
        return None

    def approach_of(self, vehicle_id: str) -> str | None:
        for o in self.observations:
            if o.vehicle_id == vehicle_id and o.action == "signal_state" and o.attributes.get("approach"):
                return o.attributes["approach"]
        for o in self.speed_samples(vehicle_id) + self.timed(lambda o: o.vehicle_id == vehicle_id):
            h = o.attributes.get("heading_deg")
            if isinstance(h, (int, float)):
                return _APPROACH_BY_HEADING.get(int(round(h / 90.0)) * 90 % 360)
        direction = next((o.attributes.get("direction") for o in self.observations
                          if o.vehicle_id == vehicle_id and o.attributes.get("direction")), None)
        if isinstance(direction, str):
            return {"northbound": "NB", "eastbound": "EB", "southbound": "SB", "westbound": "WB"}.get(direction.lower())
        return None

    def normalise_approach(self, approach: str | None) -> str | None:
        """'NB'/'northbound' as is; a dashcam's 'ego lane' becomes the ego vehicle's approach."""
        if not isinstance(approach, str):
            return None
        a = approach.strip()
        if a.upper() in APPROACH_NAMES:
            return a.upper()
        for code, name in APPROACH_NAMES.items():
            if a.lower() == name.lower():
                return code
        if "ego" in a.lower():
            ego = next((o.vehicle_id for o in self.observations if o.attributes.get("ego") and o.vehicle_id), None)
            ego = ego or next((o.vehicle_id for o in self.observations if o.source_type == SourceType.video
                               and o.vehicle_id and "ego" in (o.vehicle_description or "").lower()), None)
            return self.approach_of(ego) if ego else None
        return None

    def vehicles_on(self, approach: str) -> list[str]:
        vids = sorted({o.vehicle_id for o in self.observations if o.vehicle_id})
        return [v for v in vids if self.approach_of(v) == approach]
