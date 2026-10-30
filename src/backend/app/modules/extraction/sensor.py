"""Module 2b — sensor CSV normalisation with Pandas.

Recognises the CSV layout from its headers, so new files only need their
column names added to the alias tables below:

* vehicle tracks (telematics, OBD, ...): a time column, a speed column and
  optionally position, heading, brake and vehicle/unit id columns
* signal controller logs: time, approach and state columns
* video-derived series: optical-flow motion per camera view (``<view>_mean|p95_optical_flow...``)
  and frame indexes (time + frame number only). Their ``timestamp_s`` is seconds into
  the recording, taken as seconds from the incident reference time, as module 1 does

Instead of one observation per row it emits the moments that matter
downstream: a motion summary, braking onset, entering the intersection, a
sudden heading change, stopping, every signal change and every gap in the
record (as a ``sensor_gap`` observation plus a warning).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app import config
from app.schemas import Evidence, Observation, ObservationSet, SourceRef, SourceType

PRODUCER = "extraction.sensor/pandas"

# column name -> how to parse it
TIME_COLS = {"timestamp": "iso", "time": "iso", "datetime": "iso", "ts": "iso",
             "epoch_ms": "ms", "timestamp_ms": "ms", "epoch_s": "s", "epoch": "s",
             "timestamp_s": "rel", "time_s": "rel", "t_s": "rel", "t_rel_s": "rel", "seconds": "rel"}
# column name -> (factor to km/h, unit label for the raw attribute)
SPEED_COLS = {"speed_kmh": (1.0, "kmh"), "speed_kph": (1.0, "kmh"), "speed_mph": (1.609344, "mph"),
              "speed_ms": (3.6, "ms"), "speed_mps": (3.6, "ms")}
X_COLS = ("x_m", "pos_x", "x")
Y_COLS = ("y_m", "pos_y", "y")
HEADING_COLS = ("heading_deg", "hdg", "heading")
BRAKE_COLS = ("brake", "brake_pedal", "braking", "brake_on")
VEHICLE_COLS = ("vehicle_id", "unit_id", "vehicle", "device_id")
APPROACH_COLS = ("approach", "phase", "direction")
STATE_COLS = ("state", "signal_state", "signal", "color", "colour")
FRAME_COLS = ("source_frame_approx", "source_frame", "frame", "frame_index", "frame_no", "frame_number")
FLOW_COL = re.compile(r"^(?P<view>.+?)_(?P<stat>mean|p95|max|median)_optical_flow")

# Telemetry unit ids whose vehicle is known from the incident file.
UNIT_TO_VEHICLE = {"OBD-7731": "V2"}
SIGNAL_ALIASES = {"amber": "yellow", "orange": "yellow", "g": "green", "y": "yellow", "r": "red"}
BRAKE_TRUE = {"1", "on", "true", "yes", "y", "t"}

STOPPED_KMH = 0.5
HEADING_JUMP_DEG = 10.0
BOX_HALF_M = 3.5          # intersection box: two 3.5 m lanes each side of the centre lines
GAP_FACTOR = 1.5          # a step longer than 1.5x the median interval is a gap
DECEL_BRAKE_KMH_PER_S = 7.2  # 2 m/s^2: braking inferred from speed when there is no brake column
FLOW_SPIKE_Z = 2.5        # robust z-score (median/MAD) of a view's p95 flow that counts as a motion spike
FLOW_SPIKE_RATIO = 1.5    # ...and at least this multiple of the view's median


def _pick(cols: list[str], names) -> str | None:
    return next((c for c in names if c in cols), None)


def _row_locator(a: int, b: int | None = None) -> str:
    return f"row:{a}" if b is None or a == b else f"rows:{a}-{b}"


def _round(v, nd: int = 2):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def _parse_times(series: pd.Series, kind: str) -> pd.Series:
    if kind == "rel":  # seconds from the start of the recording = from the reference time
        secs = pd.to_numeric(series, errors="coerce")
        return pd.Timestamp(config.INCIDENT_REFERENCE_TIME) + pd.to_timedelta(secs, unit="s")
    if kind == "iso":
        ts = pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")
    else:
        unit = "ms" if kind == "ms" else "s"
        ts = pd.to_datetime(pd.to_numeric(series, errors="coerce"), unit=unit, utc=True, errors="coerce")
    return ts.dt.tz_convert(config.INCIDENT_REFERENCE_TIME.tzinfo)


def _wrap_deg(d: float) -> float:
    return (d + 180.0) % 360.0 - 180.0


class _Builder:
    """Accumulates observations and warnings for one evidence file."""

    def __init__(self, evidence: Evidence, file: str):
        self.evidence = evidence
        self.file = file
        self.observations: list[Observation] = []
        self.warnings: list[str] = []
        self._counters: dict[str, int] = {}

    def next_id(self, prefix: str, width: int = 3) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}_{n:0{width}d}"

    def add(self, obs_id: str, locator: str, ts: pd.Timestamp, t_rel: float, action: str, *,
            vehicle_id=None, description=None, attributes=None, confidence=1.0, uncertainty=0.0):
        self.observations.append(Observation(
            id=obs_id,
            source_type=SourceType.sensor,
            source_ref=SourceRef(evidence_id=self.evidence.id, file=self.file, locator=locator),
            timestamp=ts.to_pydatetime(),
            t_rel_s=round(t_rel, 3),
            time_uncertainty_s=round(uncertainty, 3),
            vehicle_id=vehicle_id,
            vehicle_description=description,
            action=action,
            attributes={k: v for k, v in (attributes or {}).items() if v is not None},
            confidence=confidence,
        ))


def _read(evidence: Evidence) -> tuple[pd.DataFrame, str]:
    path = Path(evidence.stored_path)
    df = pd.read_csv(path, dtype=str, skipinitialspace=True, keep_default_na=False)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if df.empty:
        raise ValueError(f"{evidence.filename}: no data rows")
    df["_row"] = range(2, len(df) + 2)  # 1-based, header is row 1
    return df, evidence.filename or path.name


def _with_time(df: pd.DataFrame, b: _Builder) -> pd.DataFrame:
    tcol = _pick(list(df.columns), TIME_COLS)
    df["_ts"] = _parse_times(df[tcol], TIME_COLS[tcol])
    bad = df["_ts"].isna()
    if bad.any():
        b.warnings.append(f"{b.file}: {int(bad.sum())} row(s) with unreadable '{tcol}' skipped "
                          f"(rows {', '.join(map(str, df.loc[bad, '_row']))}).")
    df = df.loc[~bad].sort_values("_ts", kind="stable").reset_index(drop=True)
    df["_t"] = (df["_ts"] - pd.Timestamp(config.INCIDENT_REFERENCE_TIME)).dt.total_seconds().round(3)
    return df


# ------------------------------------------------------------ signal logs

def _extract_signals(df: pd.DataFrame, b: _Builder) -> None:
    cols = list(df.columns)
    acol, scol = _pick(cols, APPROACH_COLS), _pick(cols, STATE_COLS)
    df = _with_time(df, b)
    last: dict[str, str] = {}
    for rec in df.to_dict("records"):
        approach = str(rec[acol]).strip().upper()
        state = str(rec[scol]).strip().lower()
        state = SIGNAL_ALIASES.get(state, state)
        if last.get(approach) == state:
            continue  # repeated status line, not a change
        b.add(b.next_id("sig"), _row_locator(rec["_row"]), rec["_ts"], rec["_t"], "signal_state",
              attributes={"approach": approach, "signal_state": state, "previous_state": last.get(approach)})
        last[approach] = state


# ---------------------------------------------------------- vehicle tracks

def _resolve_vehicle(raw: str, b: _Builder) -> tuple[str | None, str | None]:
    raw = raw.strip()
    if raw in UNIT_TO_VEHICLE:
        vid = UNIT_TO_VEHICLE[raw]
        b.warnings.append(f"{b.file}: unit_id {raw} mapped to {vid}.")
        return vid, f"unit {raw}"
    if re.fullmatch(r"V\d+", raw, flags=re.I):
        return raw.upper(), None
    if raw:
        b.warnings.append(f"{b.file}: unknown unit '{raw}' not mapped to a vehicle; vehicle_id left null.")
        return None, f"unit {raw}"
    b.warnings.append(f"{b.file}: no vehicle id column; vehicle_id left null.")
    return None, None


def _extract_track(track: pd.DataFrame, b: _Builder, vid: str | None, desc: str | None,
                   speed_unit: str, cols: dict[str, str | None]) -> None:
    prefix = f"sen_{(vid or re.sub(r'[^a-z0-9]+', '', (desc or 'unk').lower())).lower()}"
    track = track.reset_index(drop=True)
    t, row, ts = track["_t"].tolist(), track["_row"].tolist(), track["_ts"].tolist()
    spd = track["_speed"].tolist()
    xs = track["_x"].tolist() if cols["x"] else [None] * len(track)
    ys = track["_y"].tolist() if cols["y"] else [None] * len(track)
    hdg = track["_hdg"].tolist() if cols["hdg"] else [None] * len(track)
    brake = track["_brake"].tolist() if cols["brake"] else None

    def state(i: int, speed=True) -> dict:
        a = {"x_m": _round(xs[i]), "y_m": _round(ys[i]), "heading_deg": _round(hdg[i], 1)}
        if speed:
            a = {"speed_kmh": _round(spd[i], 1), **a}
            if speed_unit != "kmh":
                a[f"speed_{speed_unit}_raw"] = _round(track["_speed_raw"][i], 2)
        return a

    dts = pd.Series(t).diff().dropna()
    median_dt = float(dts.median()) if len(dts) else 0.0
    events = []  # (t, sort_key, callable) so ids are numbered in time order

    # Motion summary on the first sample.
    first_action = "stopped" if spd[0] <= STOPPED_KMH else "moving"
    summary = {"summary": {"t_start_s": t[0], "t_end_s": t[-1], "samples": len(t),
                           "sample_rate_hz": _round(1 / median_dt, 2) if median_dt else None,
                           "max_speed_kmh": _round(max(spd), 1), "min_speed_kmh": _round(min(spd), 1)}}
    events.append((t[0], 0, lambda: b.add(b.next_id(prefix), _row_locator(row[0]), ts[0], t[0], first_action,
                                          vehicle_id=vid, description=desc,
                                          attributes={**state(0), **summary}, confidence=0.98)))

    moving = spd[0] > STOPPED_KMH
    braking = bool(brake[0]) if brake else False
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]

        if median_dt and dt > GAP_FACTOR * median_dt:
            missing = int(round(dt / median_dt)) - 1
            b.warnings.append(f"{b.file}: no records between t={t[i-1]:.1f} s and t={t[i]:.1f} s "
                              f"({missing} sample(s) missing at {1 / median_dt:g} Hz).")
            events.append((t[i - 1], 1, lambda i=i, missing=missing: b.add(
                b.next_id(f"{prefix}_gap", 2), _row_locator(row[i - 1], row[i]), ts[i - 1], t[i - 1], "sensor_gap",
                vehicle_id=vid, attributes={"gap_start_s": t[i - 1], "gap_end_s": t[i], "missing_samples": missing,
                                            "expected_interval_s": round(median_dt, 3)})))

        # Braking onset: brake flag rising edge, or a hard deceleration when there is no flag.
        if brake is not None:
            onset = bool(brake[i]) and not braking
            braking = bool(brake[i])
        else:
            decel = (spd[i - 1] - spd[i]) / dt if dt > 0 else 0.0
            onset = decel >= DECEL_BRAKE_KMH_PER_S and not braking
            braking = decel >= DECEL_BRAKE_KMH_PER_S
        if onset:
            events.append((t[i], 2, lambda i=i, dt=dt: b.add(
                b.next_id(prefix), _row_locator(row[i]), ts[i], t[i], "braking", vehicle_id=vid,
                attributes={**state(i), "brake_onset_between_s": [t[i - 1], t[i]],
                            "inferred_from": "brake" if brake is not None else "deceleration"},
                confidence=0.97 if brake is not None else 0.8, uncertainty=dt)))

        if None not in (xs[i], ys[i], xs[i - 1], ys[i - 1]):
            inside = abs(xs[i]) <= BOX_HALF_M and abs(ys[i]) <= BOX_HALF_M
            was_inside = abs(xs[i - 1]) <= BOX_HALF_M and abs(ys[i - 1]) <= BOX_HALF_M
            if inside and not was_inside:
                events.append((t[i], 3, lambda i=i, dt=dt: b.add(
                    b.next_id(prefix), _row_locator(row[i]), ts[i], t[i], "entering_intersection",
                    vehicle_id=vid, attributes=state(i), confidence=0.95, uncertainty=dt)))

        if hdg[i] is not None and hdg[i - 1] is not None and not pd.isna(hdg[i]) and not pd.isna(hdg[i - 1]):
            change = _wrap_deg(hdg[i] - hdg[i - 1])
            if abs(change) >= HEADING_JUMP_DEG:
                events.append((t[i], 4, lambda i=i, change=change, dt=dt: b.add(
                    b.next_id(prefix), _row_locator(row[i]), ts[i], t[i], "sudden_heading_change",
                    vehicle_id=vid, attributes={"heading_change_deg": _round(change, 1), **state(i)},
                    confidence=0.9, uncertainty=dt)))

        if moving and spd[i] <= STOPPED_KMH:
            events.append((t[i], 5, lambda i=i, dt=dt: b.add(
                b.next_id(prefix), _row_locator(row[i]), ts[i], t[i], "stopped", vehicle_id=vid,
                attributes=state(i, speed=False), confidence=0.95, uncertainty=dt)))
        moving = spd[i] > STOPPED_KMH

    for _, _, emit in sorted(events, key=lambda e: (e[0], e[1])):
        emit()


# ------------------------------------------------------ video-derived series

def _flow_columns(cols: list[str]) -> dict[str, dict[str, str]]:
    """{view: {stat: column}} for optical-flow columns."""
    views: dict[str, dict[str, str]] = {}
    for c in cols:
        if m := FLOW_COL.match(c):
            views.setdefault(m["view"], {})[m["stat"]] = c
    return views


def _extract_motion(df: pd.DataFrame, b: _Builder, views: dict[str, dict[str, str]]) -> None:
    """Optical-flow motion per camera view -> motion spikes (candidate impacts / sudden manoeuvres).

    The flow says how much the image moved, not which vehicle moved, so vehicle_id stays null
    and confidence is modest: these are pointers for the timeline, not vehicle measurements.
    """
    df = _with_time(df, b)
    if len(df) < 2:
        return
    step = float(df["_t"].diff().median())
    b.warnings.append(f"{b.file}: timestamp_s read as seconds from the incident reference time "
                      f"(recording start assumed to be t=0; offset not verified).")
    spikes = []  # (t, view, emit) so ids are numbered in time order
    for view, stats in sorted(views.items()):
        col = stats.get("p95") or stats.get("max") or stats.get("mean") or next(iter(stats.values()))
        vals = pd.to_numeric(df[col], errors="coerce")
        missing = vals.isna()
        # Flow needs two frames, so the first sample is always empty; later blanks are gaps.
        first_valid = vals.first_valid_index()
        gaps = df.loc[missing & (df.index > (first_valid if first_valid is not None else -1)), "_row"]
        if len(gaps):
            b.warnings.append(f"{b.file}: {view} has no flow value in rows {', '.join(map(str, gaps))}.")
        valid = vals.dropna()
        if len(valid) < 5:
            b.warnings.append(f"{b.file}: {view} has too few flow samples to judge spikes.")
            continue
        med = float(valid.median())
        mad = float((valid - med).abs().median()) * 1.4826
        if mad <= 0:
            continue
        mean_col = stats.get("mean")
        for i in valid.index:
            z = (vals[i] - med) / mad
            if z < FLOW_SPIKE_Z or vals[i] < FLOW_SPIKE_RATIO * med:
                continue
            rec = df.loc[i]
            spikes.append((float(rec["_t"]), view, lambda i=i, rec=rec, z=z, view=view, col=col, med=med, mean_col=mean_col: b.add(
                  b.next_id("sen_motion"), _row_locator(int(rec["_row"])), rec["_ts"], float(rec["_t"]),
                  "motion_spike",
                  attributes={"view": view, "flow_stat": col.split("_optical_flow")[0].rsplit("_", 1)[-1],
                              "flow_px_per_frame": _round(vals[i]),
                              "mean_flow_px_per_frame": _round(pd.to_numeric(rec[mean_col], errors="coerce")) if mean_col else None,
                              "view_median_px_per_frame": _round(med), "robust_z": _round(z, 1)},
                  confidence=round(min(0.8, 0.3 + 0.1 * z), 2), uncertainty=step / 2 if step else 0.5)))
    for _, _, emit in sorted(spikes, key=lambda s: (s[0], s[1])):
        emit()
    if not b.observations:
        b.warnings.append(f"{b.file}: no motion spikes above z={FLOW_SPIKE_Z:g} in any view.")


def _describe_frame_index(df: pd.DataFrame, b: _Builder, fcol: str) -> None:
    """A time -> frame lookup carries no vehicle evidence; note it instead of failing."""
    df = _with_time(df, b)
    if df.empty:
        return
    b.warnings.append(f"{b.file}: frame index ({len(df)} rows, t={df['_t'].iloc[0]:g}–{df['_t'].iloc[-1]:g} s, "
                      f"'{fcol}' {df[fcol].iloc[0]}–{df[fcol].iloc[-1]}). It maps time to video frames and "
                      f"contains no vehicle measurements, so it yields no observations.")


def _extract_tracks(df: pd.DataFrame, b: _Builder) -> None:
    cols = list(df.columns)
    scol = _pick(cols, SPEED_COLS)
    factor, unit = SPEED_COLS[scol]
    picked = {"x": _pick(cols, X_COLS), "y": _pick(cols, Y_COLS), "hdg": _pick(cols, HEADING_COLS),
              "brake": _pick(cols, BRAKE_COLS)}
    df = _with_time(df, b)
    df["_speed_raw"] = pd.to_numeric(df[scol], errors="coerce")
    df["_speed"] = df["_speed_raw"] * factor
    bad = df["_speed"].isna()
    if bad.any():
        b.warnings.append(f"{b.file}: {int(bad.sum())} row(s) with unreadable '{scol}' skipped.")
        df = df.loc[~bad]
    for key, out in (("x", "_x"), ("y", "_y"), ("hdg", "_hdg")):
        if picked[key]:
            df[out] = pd.to_numeric(df[picked[key]], errors="coerce")
    if picked["brake"]:
        df["_brake"] = df[picked["brake"]].str.strip().str.lower().isin(BRAKE_TRUE)
    if unit != "kmh":
        b.warnings.append(f"{b.file}: speed converted from {unit} to km/h (raw value kept as speed_{unit}_raw).")

    vcol = _pick(cols, VEHICLE_COLS)
    groups = df.groupby(df[vcol].str.strip(), sort=False) if vcol else [("", df)]
    for raw_id, track in groups:
        if len(track) == 0:
            continue
        vid, desc = _resolve_vehicle(str(raw_id), b)
        _extract_track(track, b, vid, desc, unit, picked)


def extract_sensor(evidence: Evidence) -> ObservationSet:
    """Parse timestamps, normalise speed units, flag missing records."""
    df, file = _read(evidence)
    b = _Builder(evidence, file)
    cols = list(df.columns)
    has_time = _pick(cols, TIME_COLS) is not None
    if has_time and _pick(cols, APPROACH_COLS) and _pick(cols, STATE_COLS):
        _extract_signals(df, b)
    elif has_time and _pick(cols, SPEED_COLS):
        _extract_tracks(df, b)
    elif has_time and (views := _flow_columns(cols)):
        _extract_motion(df, b, views)
    elif has_time and (fcol := _pick(cols, FRAME_COLS)):
        _describe_frame_index(df, b, fcol)
    else:
        raise ValueError(f"{file}: unrecognised sensor CSV headers {cols[:-1]}. Expected a time column "
                         f"({', '.join(TIME_COLS)}) plus speed, signal state, optical-flow or frame columns.")

    b.observations.sort(key=lambda o: (o.t_rel_s if o.t_rel_s is not None else float("inf")))
    return ObservationSet(source_type=SourceType.sensor, evidence_id=evidence.id, producer=PRODUCER,
                          generated_at=datetime.now(timezone.utc), observations=b.observations,
                          warnings=b.warnings)
