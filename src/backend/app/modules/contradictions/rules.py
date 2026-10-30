"""Rule-based conflict checks. Each rule returns a list of draft conflicts (dicts
with every Conflict field except id); detect_conflicts numbers them.
"""
from __future__ import annotations

import re
from datetime import timedelta

from app.schemas import ConflictType, Observation, Severity, SourceType

from .context import APPROACH_NAMES, Context, source_label

SPEED_TOLERANCE_KMH = 15.0      # a witness estimate within this of the record is fine
SPEED_HIGH_DELTA_KMH = 20.0     # beyond this it changes the story
TREND_TOLERANCE_KMH = 3.0       # sample-to-sample change that counts as "constant"
LOWER_BOUND = {"at least", "over", "more than", "above", "minimum", "no less than", ">="}
UPPER_BOUND = {"at most", "under", "less than", "below", "maximum", "no more than", "<="}
VEHICLE_TYPES = ["suv", "sedan", "hatchback", "van", "truck", "lorry", "bus", "pickup",
                 "motorcycle", "bike", "scooter", "auto", "jeep"]
ACCEL_CLAIM = re.compile(r"\b(sped up|speeding up|accelerat\w*|floored|gunned)\b", re.I)


def _draft(type_, severity, title, description, observation_ids, t_range=None, details=None) -> dict:
    return {"type": type_, "severity": severity, "title": title, "description": description,
            "observation_ids": list(dict.fromkeys(observation_ids)),
            "t_range_s": (round(t_range[0], 2), round(t_range[1], 2)) if t_range else None,
            "details": details or {}}


def _and(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _type_name(t: str) -> str:
    return t.upper() if t == "suv" else t


def _fmt_t(t: float) -> str:
    return f"t={t:g} s"


def _clock(ctx: Context, dt) -> str:
    return dt.astimezone(ctx.timeline.reference_time.tzinfo).strftime("%H:%M:%S")


# ----------------------------------------------------------------- numeric

def speed_claims(ctx: Context) -> list[dict]:
    """Non-sensor speed figures vs the sensor speed of the same vehicle."""
    out = []
    for o in ctx.observations:
        claimed = o.attributes.get("speed_kmh")
        if o.source_type == SourceType.sensor or not o.vehicle_id or not isinstance(claimed, (int, float)):
            continue
        samples = ctx.speed_samples(o.vehicle_id)
        if not samples:
            continue
        window = ctx.window_of(o)
        if window is None:  # unplaced claim: compare with the whole pre-collision record
            t_c = ctx.collision_t()
            window = (ctx.t_of(samples[0]), t_c if t_c is not None else ctx.t_of(samples[-1]))
        in_win = [s for s in samples if window[0] <= ctx.t_of(s) <= window[1]] or samples
        speeds = [s.attributes["speed_kmh"] for s in in_win]
        qualifier = str(o.attributes.get("speed_qualifier") or "").lower().strip()

        if qualifier in LOWER_BOUND:
            measured = max(speeds)
            delta = claimed - measured
            rule = f"claimed lower bound - max measured > {SPEED_TOLERANCE_KMH:g} km/h"
        elif qualifier in UPPER_BOUND:
            measured = min(speeds)
            delta = measured - claimed
            rule = f"min measured - claimed upper bound > {SPEED_TOLERANCE_KMH:g} km/h"
        else:
            measured = min(speeds, key=lambda s: abs(s - claimed))
            delta = abs(claimed - measured)
            rule = f"|claimed - measured| > {SPEED_TOLERANCE_KMH:g} km/h"
        if delta <= SPEED_TOLERANCE_KMH:
            continue

        ref = in_win[speeds.index(measured)]
        who = source_label(o)
        what = o.vehicle_description or o.vehicle_id
        direction = "overstated" if claimed > measured else "understated"
        quoted = f"'{o.raw_text}'" if o.raw_text else f"{(qualifier + ' ') if qualifier else ''}{claimed:g} km/h"
        out.append(_draft(
            ConflictType.numeric_mismatch,
            Severity.high if delta >= SPEED_HIGH_DELTA_KMH else Severity.medium,
            f"{o.vehicle_id} speed {direction} by {who}",
            f"{who} estimates the {what} at {quoted}; {source_label(ref)} records "
            f"{measured:g} km/h between {_fmt_t(window[0])} and {_fmt_t(window[1])}.",
            [o.id, ref.id], window,
            {"claimed_kmh": claimed, "qualifier": qualifier or None, "measured_kmh": measured,
             "delta_kmh": round(abs(claimed - measured), 1), "tolerance_kmh": SPEED_TOLERANCE_KMH, "rule": rule},
        ))
    return out


# --------------------------------------------------------------- timestamps

def clock_times(ctx: Context) -> list[dict]:
    """Witness wall-clock times (± uncertainty) vs the sensor-anchored time of the same event."""
    out = []
    by_event: dict[str, tuple] = {}
    for o in ctx.observations:
        if o.source_type != SourceType.witness or o.timestamp is None:
            continue
        events = ctx.events_by_obs.get(o.id) or [e for e in ctx.timeline.events if e.category == o.action]
        if not events:
            continue
        e = events[0]
        anchors = [ctx.by_id[i] for i in e.observation_ids if i in ctx.by_id
                   and ctx.by_id[i].source_type != SourceType.witness and ctx.by_id[i].timestamp is not None]
        anchors.sort(key=lambda a: (a.source_type != SourceType.sensor, abs((ctx.t_of(a) or 0) - e.t_start_s)))
        if anchors:
            anchor = anchors[0]
            anchor_time, anchor_unc = anchor.timestamp, anchor.time_uncertainty_s
        else:
            anchor = None
            anchor_time = ctx.timeline.reference_time + timedelta(seconds=e.t_start_s)
            anchor_unc = e.uncertainty_s
        lo, hi = o.timestamp - timedelta(seconds=o.time_uncertainty_s), o.timestamp + timedelta(seconds=o.time_uncertainty_s)
        a_lo, a_hi = anchor_time - timedelta(seconds=anchor_unc), anchor_time + timedelta(seconds=anchor_unc)
        overlaps = lo <= a_hi and a_lo <= hi
        entry = by_event.setdefault(e.id, (e, anchor, anchor_time, [], []))
        entry[3 if not overlaps else 4].append((o, lo, hi))

    for e, anchor, anchor_time, bad, good in by_event.values():
        for o, lo, hi in bad:
            offset = (o.timestamp - anchor_time).total_seconds()
            miss = min(abs((lo - anchor_time).total_seconds()), abs((hi - anchor_time).total_seconds()))
            who = source_label(o)
            clock = o.attributes.get("clock_time") or o.timestamp.strftime("%H:%M")
            ok = "; ".join(f"{source_label(g)}'s ~{g.attributes.get('clock_time') or g.timestamp.strftime('%H:%M')} "
                           f"(±{g.time_uncertainty_s / 60:g} min) is consistent" for g, *_ in good)
            desc = (f"{who} puts the {e.category.replace('_', ' ')} at about {clock} (±{o.time_uncertainty_s / 60:g} min); "
                    f"{source_label(anchor) if anchor else 'the timeline'} places it at {_clock(ctx, anchor_time)}.")
            out.append(_draft(
                ConflictType.timestamp_overlap,
                Severity.high if miss > max(o.time_uncertainty_s, 300) else Severity.medium,
                f"{who}'s {e.category.replace('_', ' ')} time does not overlap sensor time",
                desc + (f" {ok}." if ok else ""),
                [o.id] + ([anchor.id] if anchor else []) + [g.id for g, *_ in good],
                (e.t_start_s, e.t_end_s),
                {"claimed_window": [_clock(ctx, lo), _clock(ctx, hi)], "sensor_time": _clock(ctx, anchor_time),
                 "offset_s": round(offset), "outside_by_s": round(miss),
                 "consistent": [g.id for g, *_ in good]},
            ))
    return out


# ----------------------------------------------------------------- semantic

def _signal_phase(ctx: Context, approach: str, t: float):
    """(state, obs, phase_start, phase_end) from the controller log at time t, or None."""
    log = ctx.timed(lambda o: o.source_type == SourceType.sensor and o.action == "signal_state"
                    and o.attributes.get("approach") == approach)
    current = None
    for i, s in enumerate(log):
        if ctx.t_of(s) <= t:
            nxt = ctx.t_of(log[i + 1]) if i + 1 < len(log) else None
            current = (s.attributes.get("signal_state"), s, ctx.t_of(s), nxt)
    return current


def signal_states(ctx: Context) -> list[dict]:
    """Signal-state claims vs the controller log for the same approach and time."""
    claims_by_approach: dict[str, list[tuple[Observation, tuple, tuple]]] = {}
    for o in ctx.observations:
        approach = ctx.normalise_approach(o.attributes.get("approach"))
        if approach is None and o.vehicle_id and o.source_type == SourceType.witness:
            approach = ctx.approach_of(o.vehicle_id)  # "its light was green": the vehicle's own approach
        if o.action != "signal_state" or o.source_type == SourceType.sensor or not approach:
            continue
        entry = ctx.entry_t(o.vehicle_id) if o.source_type == SourceType.witness and o.vehicle_id else None
        if entry is not None:
            # "its light was green when it was approaching": the decisive moment is the entry.
            # Checked even when the timeline placed the claim elsewhere: module 3 groups signal
            # claims by their stated colour, which would make every claim agree with itself.
            probe = entry[0] - 0.05
        else:
            window = ctx.window_of(o)
            if window is None:
                continue
            probe = (window[0] + window[1]) / 2 if o.source_type == SourceType.witness else ctx.t_of(o)
        phase = _signal_phase(ctx, approach, probe)
        if phase is None or phase[0] is None:
            continue
        claims_by_approach.setdefault(approach, []).append((o, phase, probe))

    out = []
    for approach, claims in claims_by_approach.items():
        disputed = [(o, ph) for o, ph, _ in claims if o.attributes.get("signal_state") != ph[0]]
        if not disputed:
            continue
        for o, phase in disputed:
            state, ref, t0, t1 = phase
            t1 = t1 if t1 is not None else t0
            agreeing = [ref.id] + [c.id for c, ph, _ in claims if ph[2] == t0 and c.attributes.get("signal_state") == state]
            claimed = o.attributes.get("signal_state")
            who = source_label(o)
            subject = f"{o.vehicle_id}'s light" if o.vehicle_id else "the light"
            others = [ctx.by_id[i] for i in agreeing[1:]]
            names = [("the " + n if n == "dashcam" else n) for n in (source_label(a) for a in others)]
            agree_txt = f" {_and(names)[0].upper()}{_and(names)[1:]} also report{'s' if len(others) == 1 else ''} {state}." if others else ""
            entered = ""
            if o.vehicle_id and (entry := ctx.entry_t(o.vehicle_id)):
                entered = f" {o.vehicle_id} entered the junction at t≈{entry[0]:g} s."
            right_of_way = {claimed, state} & {"red"} or {claimed, state} == {"green", "yellow"}
            out.append(_draft(
                ConflictType.semantic,
                Severity.high if right_of_way else Severity.medium,
                f"{APPROACH_NAMES.get(approach, approach)} signal state disputed",
                f"{who} says {subject} was {claimed}. The signal controller shows {state} "
                f"from {_fmt_t(t0)} to {_fmt_t(t1)}.{entered}{agree_txt}",
                [o.id] + agreeing, (t0, t1),
                {"method": "rule:signal_phase", "approach": approach, "claimed_state": claimed,
                 "recorded_state": state, "agreeing": agreeing},
            ))
    return out


def _trend(speeds: list[float]) -> str:
    diffs = [b - a for a, b in zip(speeds, speeds[1:])]
    if any(d > TREND_TOLERANCE_KMH for d in diffs):
        return "increasing"
    falling = [d < -TREND_TOLERANCE_KMH for d in diffs]
    if not any(falling):
        return "constant"
    if all(falling):
        return "decreasing"
    return "constant_then_decreasing" if not falling[0] else "decreasing"


def speed_trends(ctx: Context) -> list[dict]:
    """'Sped up' / 'accelerating' claims vs the measured speed profile."""
    out = []
    for o in ctx.observations:
        if o.source_type == SourceType.sensor or not o.vehicle_id:
            continue
        claim = " ".join(str(x) for x in (o.attributes.get("claim"), o.raw_text) if x)
        if o.action != "accelerating" and not ACCEL_CLAIM.search(claim):
            continue
        samples = ctx.speed_samples(o.vehicle_id)
        if len(samples) < 2:
            continue
        entry = ctx.entry_t(o.vehicle_id) if o.source_type == SourceType.witness else None
        if entry is not None:
            # A witness's "sped up to beat the light" is about the run-up to the junction, from the
            # onset of the phase the vehicle entered on, wherever the timeline placed the claim.
            approach = ctx.approach_of(o.vehicle_id)
            phase = _signal_phase(ctx, approach, entry[0] - 0.05) if approach else None
            window = (phase[2] if phase else entry[0] - 3.0, entry[0])
        else:
            window = ctx.window_of(o)
            if window is None:
                continue
        # Keep the last sample before the window as the baseline speed.
        before = [s for s in samples if ctx.t_of(s) < window[0]]
        used = before[-1:] + [s for s in samples if window[0] <= ctx.t_of(s) <= window[1]]
        if len(used) < 2:
            continue
        trend = _trend([s.attributes["speed_kmh"] for s in used])
        if trend == "increasing":
            continue
        braking = [s for s in ctx.timed(lambda s: s.source_type == SourceType.sensor and s.vehicle_id == o.vehicle_id
                                        and s.action == "braking") if window[0] - 1 <= ctx.t_of(s) <= window[1]]
        profile = ", ".join(f"{s.attributes['speed_kmh']:g} km/h at {_fmt_t(ctx.t_of(s))}" for s in used)
        brake_txt = f", braking from {_fmt_t(ctx.t_of(braking[0]))}" if braking else ""
        who = source_label(o)
        quoted = f"'{o.attributes.get('claim') or o.raw_text}'"
        out.append(_draft(
            ConflictType.semantic,
            Severity.medium,
            f"{who} claims {o.vehicle_id} accelerated; telemetry shows {'braking' if braking else trend.replace('_', ' ')}",
            f"{who} says the {o.vehicle_description or o.vehicle_id} {quoted}. {o.vehicle_id} telemetry shows "
            f"{trend.replace('_', ' ')} speed ({profile}){brake_txt}.",
            [o.id] + [s.id for s in used] + [b.id for b in braking], window,
            {"method": "rule:speed_trend", "measured_trend": trend, "tolerance_kmh": TREND_TOLERANCE_KMH,
             "speeds_kmh": [s.attributes["speed_kmh"] for s in used]},
        ))
    return out


def _types_in(text: str | None) -> list[str]:
    if not text:
        return []
    low = text.lower()
    return [t for t in VEHICLE_TYPES if re.search(rf"\b{t}\b", low)]


def vehicle_types(ctx: Context) -> list[dict]:
    """Same vehicle described with different or hedged body types."""
    out = []
    for vid in sorted({o.vehicle_id for o in ctx.observations if o.vehicle_id}):
        definite: dict[str, list[Observation]] = {}
        hedged: list[tuple[Observation, list[str]]] = []
        for o in (o for o in ctx.observations if o.vehicle_id == vid):
            alts = [str(a).lower() for a in o.attributes.get("vehicle_type_alternatives") or []]
            types = alts or _types_in(o.vehicle_description)
            if len(types) > 1:
                hedged.append((o, types))
            elif types:
                definite.setdefault(types[0], []).append(o)
        if len(definite) > 1:
            names = sorted(definite)
            ids = [o.id for t in names for o in definite[t]]
            out.append(_draft(
                ConflictType.semantic, Severity.medium, f"{vid} vehicle type inconsistent",
                f"Sources describe {vid} as " + " vs ".join(
                    f"{_type_name(t)} ({', '.join(sorted({source_label(o) for o in definite[t]}))})" for t in names) + ".",
                ids, None, {"method": "rule:vehicle_type", "types": names},
            ))
        for o, types in hedged:
            support = [t for t in types if t in definite]
            backers = [x for t in support for x in definite[t]]
            backers.sort(key=lambda x: (x.source_type != SourceType.video, x.id))
            nice = [_type_name(t) for t in types]
            agree = (f"; {_and(sorted({source_label(b) for b in backers}))} say "
                     f"{'/'.join(_type_name(t) for t in support)}") if backers else ""
            out.append(_draft(
                ConflictType.semantic, Severity.low, f"{vid} vehicle type ambiguous",
                f"{source_label(o)} is unsure whether {vid} was {' or '.join(('an ' if n[0] in 'aeiouAEIOU' or n == 'SUV' else 'a ') + n for n in nice)}{agree}.",
                [o.id] + [b.id for b in backers], None,
                {"method": "rule:vehicle_type", "alternatives": types, "supported": support},
            ))
    return out


# ---------------------------------------------------------- missing evidence

def sensor_gaps(ctx: Context) -> list[dict]:
    """Sensor dropouts that overlap key moments (signal changes, event onsets, the collision)."""
    out = []
    t_c = ctx.collision_t()
    for g in ctx.observations:
        if g.action != "sensor_gap":
            continue
        start = g.attributes.get("gap_start_s", ctx.t_of(g))
        end = g.attributes.get("gap_end_s", start)
        if start is None:
            continue
        changes = ctx.timed(lambda o: o.action == "signal_state" and o.source_type == SourceType.sensor
                            and start <= ctx.t_of(o) <= end)
        onsets = [e for e in ctx.timeline.events if start <= e.t_start_s <= end and g.id not in e.observation_ids]
        hits_collision = t_c is not None and start <= t_c <= end
        if not (changes or onsets or hits_collision):
            continue
        who = g.vehicle_id or "sensor"
        moments = [f"the {APPROACH_NAMES.get(c.attributes.get('approach'), 'traffic').lower()} signal changed to "
                   f"{c.attributes.get('signal_state')} at {_fmt_t(ctx.t_of(c))}" for c in changes]
        moments += [f"'{e.label}' began at {_fmt_t(e.t_start_s)}" for e in onsets
                    if not any(c.id in e.observation_ids for c in changes)]
        if hits_collision:
            moments.append(f"the collision at {_fmt_t(t_c)}")
        what = changes[0].attributes.get("signal_state") + " onset" if changes else "a key event"
        reaction = (f" {who}'s reaction to the {changes[0].attributes.get('signal_state')} cannot be confirmed from telemetry."
                    if changes and g.vehicle_id else "")
        details = {k: g.attributes[k] for k in ("missing_samples", "sample_rate_hz") if k in g.attributes}
        out.append(_draft(
            ConflictType.missing_evidence,
            Severity.high if hits_collision else Severity.medium,
            f"{who} telemetry dropout during {'the collision' if hits_collision else what}",
            f"No {who} records between {_fmt_t(start)} and {_fmt_t(end)}, while {'; '.join(moments)}.{reaction}",
            [g.id] + [c.id for c in changes] + [i for e in onsets for i in e.observation_ids[:1]],
            (start, end),
            {**details, "gap_s": round(end - start, 2), "overlapping_event_ids": [e.id for e in onsets]},
        ))
    return out


def single_source_events(ctx: Context) -> list[dict]:
    """Decisive timeline events backed by one source type only.

    Only the collision itself and signal phases a vehicle entered the junction on
    decide fault, so only those are flagged. Flagging every single-source event
    buried the report (15 of 17 conflicts with sensor-only evidence).
    """
    out = []
    t_c = ctx.collision_t()
    for e in ctx.timeline.events:
        if len(set(e.source_types)) != 1 or e.category not in ("collision", "signal"):
            continue
        if e.category == "signal" and t_c is not None and e.t_start_s >= t_c:
            continue  # phases that start at or after the impact can't have been entered on
        only = e.source_types[0]
        ids = list(e.observation_ids)
        extra = ""
        t_range = (e.t_start_s, e.t_end_s)
        if e.category == "signal":
            approach = next((ctx.by_id[i].attributes.get("approach") for i in ids if i in ctx.by_id), None)
            vids = ctx.vehicles_on(approach) if approach else []
            # Probe just before the entry, as signal_states does: a phase starting at the entry instant
            # was not the one the vehicle entered on.
            entries = [x for x in ctx.timed(lambda o: o.vehicle_id in vids and o.action == "entering_intersection")
                       if e.t_start_s <= ctx.t_of(x) - 0.05 <= e.t_end_s]
            if not entries:
                continue
            ids += [x.id for x in entries]
            extra = (f" {', '.join(sorted({x.vehicle_id for x in entries}))}'s entry on this signal "
                     f"rests on the {only.value} record alone; no camera or witness saw the signal head.")
            # The window that matters is from the entry to the collision.
            t_range = (ctx.t_of(entries[0]), t_c if t_c is not None else e.t_end_s)
        label = {"sensor": "the sensor/controller log", "video": "the dashcam", "witness": "witness accounts"}[only.value]
        out.append(_draft(
            ConflictType.missing_evidence,
            Severity.medium if e.category == "collision" else Severity.low,
            f"'{e.label}' has a single source",
            f"'{e.label}' ({_fmt_t(e.t_start_s)}–{e.t_end_s:g} s) is supported only by {label}.{extra}",
            ids, t_range,
            {"event_id": e.id, "source_types": [only.value]},
        ))
    return out


ALL_RULES = [speed_claims, signal_states, speed_trends, clock_times, sensor_gaps, single_source_events, vehicle_types]
