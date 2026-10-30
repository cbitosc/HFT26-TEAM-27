"""Rule-based conflict checks. Each rule returns a list of draft conflicts (dicts
with every Conflict field except id); detect_conflicts numbers them.
"""
from __future__ import annotations

import re
from datetime import timedelta

from app.schemas import ConflictType, Observation, Severity, SourceType

from .context import APPROACH_NAMES, SOURCE_RANK, Context, source_label, witness_label

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
    """(state, obs, phase_start, phase_end) at time t from the controller log, or, when there is no log
    for that approach, from video readings of the signal (an AI reading: see the obs basis)."""
    log = ctx.timed(lambda o: o.source_type == SourceType.sensor and o.action == "signal_state"
                    and o.attributes.get("approach") == approach)
    if not log:
        log = ctx.timed(lambda o: o.source_type == SourceType.video and o.action == "signal_state"
                        and ctx.normalise_approach(o.attributes.get("approach")) == approach)
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
            from_video = ref.source_type == SourceType.video
            record = "the camera footage (AI reading of the signal)" if from_video else "The signal controller"
            out.append(_draft(
                ConflictType.semantic,
                Severity.medium if from_video or not right_of_way else Severity.high,
                f"{APPROACH_NAMES.get(approach, approach)} signal state disputed",
                f"{who} says {subject} was {claimed}. {record[0].upper() + record[1:]} shows {state} "
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


# Witness action -> record actions that say the opposite (for the same vehicle, around the same time).
OPPOSITES = {"no_braking": {"braking"}, "accelerating": {"braking", "stopped"}, "stopped": {"moving"},
             "braking": {"accelerating"}}


NO_BRAKING_LOOKBACK_S = 15.0


def opposing_actions(ctx: Context) -> list[dict]:
    """A witness says a vehicle did X; a sensor, the video or tracking shows the opposite then."""
    out = []
    t_c = ctx.collision_t()
    for o in ctx.observations:
        opposite = OPPOSITES.get(o.action)
        if o.source_type != SourceType.witness or not opposite or not o.vehicle_id:
            continue
        if o.action == "accelerating" and ctx.speed_samples(o.vehicle_id):
            continue  # speed_trends already judges this against the measured speed profile
        window = ctx.window_of(o)
        if window is None:  # untimed claim: the run-up to the collision
            if t_c is None:
                continue
            window = (t_c - 5.0, t_c)
        lo, hi = window[0] - 1.0, window[1] + 1.0
        if o.action == "no_braking":  # "did not brake before it hit": any braking over the run-up contradicts it
            lo = min(lo, hi - NO_BRAKING_LOOKBACK_S)
        record = [r for r in ctx.observations if r.source_type != SourceType.witness and r.vehicle_id == o.vehicle_id
                  and r.action in opposite and (t := ctx.t_of(r)) is not None and lo <= t <= hi]
        if o.action == "stopped":  # "stopped" is contradicted only by real movement, not by a moving summary at t0
            record = [r for r in record if (r.attributes.get("speed_kmh") or 0) > 10]
        if not record:
            continue
        r = min(record, key=lambda r: abs((ctx.t_of(r) or 0) - (window[0] + window[1]) / 2))
        weak = r.basis == "inferred"
        out.append(_draft(
            ConflictType.semantic, Severity.low if weak else Severity.medium,
            f"{source_label(o)} says {o.vehicle_id} {o.action.replace('_', ' ')}; {source_label(r)} shows "
            f"{r.action.replace('_', ' ')}",
            f"{source_label(o)}: '{o.raw_text or o.action}'. {source_label(r)} records {r.action.replace('_', ' ')} "
            f"for {o.vehicle_id} at {_fmt_t(ctx.t_of(r))} ({r.basis}"
            f"{', an AI reading of the footage' if weak else ''}).",
            [o.id, r.id], (lo, hi), {"method": "rule:opposing_action", "record_basis": r.basis}))
    return out


def colours(ctx: Context) -> list[dict]:
    """Sources that describe the same vehicle in different colours."""
    from app.modules.extraction.witness import _words

    out = []
    for vid in sorted({o.vehicle_id for o in ctx.observations if o.vehicle_id}):
        seen: dict[str, list[Observation]] = {}
        for o in ctx.observations:
            if o.vehicle_id == vid and o.vehicle_description:
                cs, _ = _words(o.vehicle_description)
                if len(cs) == 1:
                    seen.setdefault(next(iter(cs)), []).append(o)
        if len(seen) > 1:
            names = sorted(seen)
            out.append(_draft(
                ConflictType.semantic, Severity.medium, f"{vid} described in different colours",
                f"Sources describe {vid} as " + " vs ".join(
                    f"{c} ({', '.join(sorted({source_label(o) for o in seen[c]}))})" for c in names) + ".",
                [o.id for c in names for o in seen[c][:2]], None, {"method": "rule:colour", "colours": names}))
    return out


def coverage_gaps(ctx: Context) -> list[dict]:
    """Evidence that stops (or starts) before the collision: what it cannot tell us."""
    t_c = ctx.collision_t()
    if t_c is None or not ctx.coverage:
        return []
    out = []
    videos = [c for c in ctx.coverage if c[0] == SourceType.video]
    if videos and not any(c[3][0] - 0.5 <= t_c <= c[3][1] + 0.5 for c in videos):
        spans = ", ".join(f"{f} ({c[0]:g}–{c[1]:g} s)" for _, f, _, c in videos)
        ids = [o.id for o in ctx.observations if o.source_type == SourceType.video][:1]
        if ids:
            out.append(_draft(ConflictType.missing_evidence, Severity.medium, "No video covers the collision",
                              f"The collision is placed at {_fmt_t(t_c)}, but the footage covers only {spans}.",
                              ids, (t_c, t_c), {"method": "rule:coverage", "collision_t_s": t_c}))
    coll = next((e for e in ctx.timeline.events if e.category == "collision"), None)
    for vid in (coll.vehicle_ids if coll else []):
        own = [c for c in ctx.coverage if c[0] == SourceType.sensor and vid in c[2]]
        if own and not any(c[3][0] - 0.5 <= t_c <= c[3][1] + 0.5 for c in own):
            f, (a, b) = own[0][1], own[0][3]
            ids = [o.id for o in ctx.observations if o.vehicle_id == vid and o.source_ref.file == f][:1]
            if ids:
                out.append(_draft(ConflictType.missing_evidence, Severity.medium,
                                  f"{vid} telemetry does not cover the collision",
                                  f"{f} covers {a:g}–{b:g} s; the collision is placed at {_fmt_t(t_c)}.",
                                  ids, (t_c, t_c), {"method": "rule:coverage"}))
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


# ------------------------------------------------- source against source

SOURCE_TIME_TOLERANCE_S = 1.0   # two sources may place one event this far apart (plus their own uncertainty)
SOURCE_TIME_HIGH_S = 5.0
MIN_MATCHED_SAMPLES = 3
TIMED_EVENT_ACTIONS = ("collision", "entering_intersection")


def source_speeds(ctx: Context) -> list[dict]:
    """Two sensors report different speeds for one vehicle at the same moments (e.g. GPS vs OBD)."""
    out = []
    for vid in sorted({o.vehicle_id for o in ctx.observations if o.vehicle_id}):
        by_file: dict[str, list[Observation]] = {}
        for s in ctx.speed_samples(vid):
            by_file.setdefault(s.source_ref.evidence_id, []).append(s)
        files = sorted(by_file)
        for i, fa in enumerate(files):
            for fb in files[i + 1:]:
                pairs = []
                for a in by_file[fa]:
                    near = min(by_file[fb], key=lambda b: abs(ctx.t_of(b) - ctx.t_of(a)))
                    if abs(ctx.t_of(near) - ctx.t_of(a)) <= SOURCE_TIME_TOLERANCE_S + a.time_uncertainty_s + near.time_uncertainty_s:
                        pairs.append((a, near, abs(a.attributes["speed_kmh"] - near.attributes["speed_kmh"])))
                far = [p for p in pairs if p[2] > SPEED_TOLERANCE_KMH]
                if len(pairs) < MIN_MATCHED_SAMPLES or len(far) * 2 < len(pairs):
                    continue
                a, b, d = max(far, key=lambda p: p[2])
                lo, hi = min(ctx.t_of(p[0]) for p in far), max(ctx.t_of(p[0]) for p in far)
                out.append(_draft(
                    ConflictType.numeric_mismatch, Severity.high if d >= SPEED_HIGH_DELTA_KMH else Severity.medium,
                    f"{vid} speed differs between {a.source_ref.file} and {b.source_ref.file}",
                    f"{len(far)} of {len(pairs)} paired readings differ by more than {SPEED_TOLERANCE_KMH:g} km/h "
                    f"(largest: {a.attributes['speed_kmh']:g} vs {b.attributes['speed_kmh']:g} km/h near {_fmt_t(ctx.t_of(a))}).",
                    [a.id, b.id], (lo, hi),
                    {"method": "rule:source_speed", "matched": len(pairs), "disagreeing": len(far),
                     "max_delta_kmh": round(d, 1), "tolerance_kmh": SPEED_TOLERANCE_KMH}))
    return out


def source_event_times(ctx: Context) -> list[dict]:
    """Two kinds of source (sensor / video) place the same event (collision, a vehicle's junction entry) at
    times that do not overlap, uncertainty included."""
    out = []
    keyed: dict[tuple, dict[SourceType, Observation]] = {}
    for o in ctx.timed(lambda o: o.source_type != SourceType.witness and o.action in TIMED_EVENT_ACTIONS):
        key = (o.action, None if o.action == "collision" else o.vehicle_id)
        if o.action != "collision" and not o.vehicle_id:
            continue
        keyed.setdefault(key, {}).setdefault(o.source_type, o)  # earliest per source
    for (action, vid), by_src in keyed.items():
        srcs = sorted(by_src, key=lambda s: SOURCE_RANK[s])
        for i, sa in enumerate(srcs):
            for sb in srcs[i + 1:]:
                a, b = by_src[sa], by_src[sb]
                gap = abs(ctx.t_of(a) - ctx.t_of(b)) - a.time_uncertainty_s - b.time_uncertainty_s
                if gap <= SOURCE_TIME_TOLERANCE_S:
                    continue
                what = action.replace("_", " ") + (f" of {vid}" if vid else "")
                out.append(_draft(
                    ConflictType.timestamp_overlap, Severity.high if gap >= SOURCE_TIME_HIGH_S else Severity.medium,
                    f"{what.capitalize()} time disagrees between {source_label(a)} and {source_label(b)}",
                    f"{source_label(a)} places the {what} at {_fmt_t(ctx.t_of(a))} (±{a.time_uncertainty_s:g} s), "
                    f"{source_label(b)} at {_fmt_t(ctx.t_of(b))} (±{b.time_uncertainty_s:g} s): the windows are "
                    f"{gap:.1f} s apart.",
                    [a.id, b.id], tuple(sorted((ctx.t_of(a), ctx.t_of(b)))),
                    {"method": "rule:source_event_time", "gap_s": round(gap, 2), "action": action}))
    return out


def witness_signals(ctx: Context) -> list[dict]:
    """Two witnesses name different light colours for one approach and there is no controller log or footage
    to say which is right (signal_states judges claims against a record when there is one)."""
    claims: dict[str, list[Observation]] = {}
    for o in ctx.observations:
        if o.action != "signal_state" or o.source_type != SourceType.witness:
            continue
        approach = ctx.normalise_approach(o.attributes.get("approach"))
        if approach is None and o.vehicle_id:
            approach = ctx.approach_of(o.vehicle_id)
        if approach and o.attributes.get("signal_state"):
            claims.setdefault(approach, []).append(o)
    out = []
    for approach, cs in claims.items():
        if any(r.source_type != SourceType.witness and r.action == "signal_state"
               and (r.attributes.get("approach") == approach or ctx.normalise_approach(r.attributes.get("approach")) == approach)
               for r in ctx.observations):
            continue
        for i, a in enumerate(cs):
            for b in cs[i + 1:]:
                if witness_label(a) == witness_label(b) or a.attributes["signal_state"] == b.attributes["signal_state"]:
                    continue
                wa, wb = ctx.window_of(a), ctx.window_of(b)
                if wa and wb and (wa[0] > wb[1] or wb[0] > wa[1]):
                    continue  # different moments: the light may have changed
                out.append(_draft(
                    ConflictType.semantic, Severity.medium, f"{APPROACH_NAMES.get(approach, approach)} signal state disputed between witnesses",
                    f"{source_label(a)} says {a.attributes['signal_state']}, {source_label(b)} says {b.attributes['signal_state']}; "
                    f"no controller log or footage settles it.",
                    [a.id, b.id], wa or wb, {"method": "rule:witness_signal", "approach": approach}))
    return out


def event_order(ctx: Context) -> list[dict]:
    """A witness tells the order in which vehicles did the same thing ("the red car went in first, then the
    white one"); the record shows the opposite order."""
    out = []
    stmts: dict[str, list[Observation]] = {}
    for o in ctx.observations:
        if o.source_type == SourceType.witness and o.vehicle_id and isinstance(o.attributes.get("order"), int) \
                and o.action in TIMED_EVENT_ACTIONS + ("braking",):
            stmts.setdefault(f"{o.source_ref.evidence_id}|{o.source_ref.file}", []).append(o)
    for claims in stmts.values():
        claims.sort(key=lambda o: o.attributes["order"])
        for i, a in enumerate(claims):
            for b in claims[i + 1:]:
                if a.action != b.action or a.vehicle_id == b.vehicle_id or a.attributes["order"] == b.attributes["order"]:
                    continue
                rec = lambda o: min((r for r in ctx.observations if r.source_type != SourceType.witness  # noqa: E731
                                     and r.vehicle_id == o.vehicle_id and r.action == o.action
                                     and ctx.t_of(r) is not None), key=lambda r: (SOURCE_RANK[r.source_type], ctx.t_of(r)), default=None)
                ra, rb = rec(a), rec(b)
                if not ra or not rb:
                    continue
                gap = ctx.t_of(ra) - ctx.t_of(rb) - ra.time_uncertainty_s - rb.time_uncertainty_s
                if gap <= SOURCE_TIME_TOLERANCE_S:
                    continue
                what = a.action.replace("_", " ")
                out.append(_draft(
                    ConflictType.semantic, Severity.medium,
                    f"{source_label(a)} orders {a.vehicle_id} before {b.vehicle_id} ({what}); the record has it the other way",
                    f"{source_label(a)} tells {a.vehicle_id}'s {what} first and {b.vehicle_id}'s after it; "
                    f"{source_label(ra)} puts {a.vehicle_id} at {_fmt_t(ctx.t_of(ra))} and {source_label(rb)} puts "
                    f"{b.vehicle_id} at {_fmt_t(ctx.t_of(rb))}.",
                    [a.id, b.id, ra.id, rb.id], (ctx.t_of(rb), ctx.t_of(ra)),
                    {"method": "rule:event_order", "claimed_first": a.vehicle_id, "recorded_first": b.vehicle_id}))
    return out


ALL_RULES = [speed_claims, signal_states, speed_trends, opposing_actions, clock_times, sensor_gaps, coverage_gaps,
             single_source_events, vehicle_types, colours, source_speeds, source_event_times, witness_signals,
             event_order]
