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

import math
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app import config
from app.schemas import Evidence, Observation, ObservationSet, SourceRef, SourceType

PRODUCER = "extraction.sensor/pandas"

# column name -> how to parse it
TIME_COLS = {"timestamp": "iso", "time": "iso", "datetime": "iso", "ts": "iso",
             "epoch_ms": "ms", "timestamp_ms": "ms", "epoch_s": "s", "epoch": "s",
             "timestamp_s": "rel", "time_s": "rel", "t_s": "rel", "t_rel_s": "rel", "seconds": "rel"}
# column name -> (factor to km/h, unit label for the raw attribute)
# unit -> (factor to km/h, label kept in the raw attribute name). Matched on the column header tokens.
SPEED_UNITS = {"kmh": (1.0, "kmh"), "kph": (1.0, "kmh"), "mph": (1.609344, "mph"),
               "ms": (3.6, "ms"), "mps": (3.6, "ms"), "knots": (1.852, "knots"), "kn": (1.852, "knots"),
               "kt": (1.852, "knots"), "kts": (1.852, "knots"), "fps": (1.09728, "fps"), "fts": (1.09728, "fps")}
SPEED_WORD = re.compile(r"(^|_)(speed|velocity|vel|spd|speedometer)($|_)")
NOT_SPEED = re.compile(r"limit|max|min|avg|mean|target|set")


def _tokens(col: str) -> list[str]:
    """Header tokens with units normalised: 'Speed (km/h)' -> speed, kmh; 'v_m/s' -> v, ms."""
    c = re.sub(r"km\s*/\s*h", "kmh", col.lower())
    c = re.sub(r"m\s*/\s*s", "ms", c)
    c = re.sub(r"(?:ft|feet)\s*/\s*s", "fts", c)
    return [t for t in re.split(r"[^a-z0-9]+", c) if t]


def speed_column(cols: list[str]) -> tuple[str, float, str, bool] | None:
    """(column, factor to km/h, unit label, unit_assumed) for the speed column, or None.

    The unit comes from the header (any of kmh, kph, mph, m/s, knots, ft/s). A speed column
    with no unit in its name is read as km/h but flagged as assumed, never silently trusted."""
    found = None
    for c in cols:
        if c.startswith("_") or NOT_SPEED.search(c):
            continue
        toks = _tokens(c)
        unit = next((t for t in toks if t in SPEED_UNITS), None)
        named = SPEED_WORD.search("_" + "_".join(toks) + "_") or toks[0] in ("v", "spd")
        # a header that is only a speed-like unit (kmh, mph, knots) is a speed; "ms" alone or "epoch_ms" is time
        unit_only = len(toks) == 1 and unit in ("kmh", "kph", "mph", "knots", "kts", "mps")
        if unit and (named or unit_only):
            return (c, *SPEED_UNITS[unit], False)
        if found is None and SPEED_WORD.search("_" + "_".join(toks) + "_"):
            found = (c, 1.0, "kmh", True)
    return found
X_COLS = ("x_m", "pos_x", "x", "east_m", "easting")
Y_COLS = ("y_m", "pos_y", "y", "north_m", "northing")
LAT_COLS = ("lat", "latitude", "gps_lat")
LON_COLS = ("lon", "lng", "long", "longitude", "gps_lon", "gps_lng")
HEADING_COLS = ("heading_deg", "hdg", "heading")
BRAKE_COLS = ("brake", "brake_pedal", "braking", "brake_on")
VEHICLE_COLS = ("vehicle_id", "unit_id", "vehicle", "device_id", "device", "unit", "tracker_id", "imei", "car_id")
TIME_NAME = re.compile(r"(^|_)(time|times|ts|timestamp|epoch|date|datetime|stamp|unix|utc)($|_)")
VEHICLE_NAME = re.compile(r"(^|_)(vehicle|unit|device|tracker|imei|car|truck|bus)(_?id|_?name|_?no)?$")
APPROACH_COLS = ("approach", "phase", "direction")
STATE_COLS = ("state", "signal_state", "signal", "color", "colour")
FRAME_COLS = ("source_frame_approx", "source_frame", "frame", "frame_index", "frame_no", "frame_number")
FLOW_COL = re.compile(r"^(?P<view>.+?)_(?P<stat>mean|p95|max|median)_optical_flow")

SIGNAL_ALIASES = {"amber": "yellow", "orange": "yellow", "g": "green", "y": "yellow", "r": "red"}
BRAKE_TRUE = {"1", "on", "true", "yes", "y", "t"}

STOPPED_KMH = 0.5
HEADING_JUMP_DEG = 10.0
GAP_FACTOR = 1.5          # a step longer than 1.5x the median interval is a gap
DECEL_BRAKE_KMH_PER_S = 7.2  # 2 m/s^2: braking inferred from speed when there is no brake column
FLOW_SPIKE_Z = 2.5        # robust z-score (median/MAD) of a view's p95 flow that counts as a motion spike
FLOW_SPIKE_RATIO = 1.5    # ...and at least this multiple of the view's median


MIN_PARSE_SHARE = 0.5   # share of non-empty cells that must parse for a column to be the time column


def _pick(cols: list[str], names) -> str | None:
    return next((c for c in names if c in cols), None)


def _time_kind(values: pd.Series, name: str) -> str | None:
    """iso | ms | s | rel from the values themselves (names only break ties).

    A column counts as time when most of its values parse; stray unreadable cells are skipped
    row by row later, with a warning, instead of rejecting the whole file."""
    v = values.astype(str).str.strip()
    v = v[v != ""].head(200)
    if v.empty:
        return None
    nums = pd.to_numeric(v, errors="coerce")
    if nums.notna().mean() >= MIN_PARSE_SHARE:
        med = float(nums.median())
        if name.endswith("_ms") or med > 1e11:
            return "ms"
        if med > 1e8:
            return "s"      # epoch seconds (after 1973)
        return "rel"        # seconds from the start of the recording
    if pd.to_datetime(v, format="ISO8601", errors="coerce", utc=True).notna().mean() >= MIN_PARSE_SHARE:
        return "iso"
    return None


def time_column(df: pd.DataFrame) -> tuple[str, str] | None:
    """(column, kind) for the time column: known names first, then any time-like name whose values parse."""
    cols = [c for c in df.columns if not c.startswith("_")]
    ordered = [c for c in TIME_COLS if c in cols] + [c for c in cols if c not in TIME_COLS and TIME_NAME.search(c)]
    for c in ordered:
        found = _time_kind(df[c], c)
        if found is None:
            continue
        if c in TIME_COLS:  # an explicit name ("epoch_s", "timestamp_ms") says what the numbers mean
            declared = TIME_COLS[c]
            return c, declared if (declared == "iso") == (found == "iso") else found
        if "unix" in c or "epoch" in c:
            return c, "ms" if found == "ms" or c.endswith("_ms") else "s"
        return c, found
    return None


def vehicle_column(cols: list[str]) -> str | None:
    return _pick(cols, VEHICLE_COLS) or next((c for c in cols if VEHICLE_NAME.search(c) and not TIME_NAME.search(c)), None)


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
        self.coverage: list[float] = []

    def next_id(self, prefix: str, width: int = 3) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}_{n:0{width}d}"

    # Read straight off the file vs computed from several rows (thresholds, differences, gaps).
    MEASURED = {"moving", "signal_state", "stopped", "track"}

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
            basis="measured" if action in self.MEASURED or (action == "braking" and (attributes or {}).get(
                "inferred_from") == "brake") else "derived",
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
    tcol, kind = time_column(df)
    df["_ts"] = _parse_times(df[tcol], kind)
    bad = df["_ts"].isna()
    if bad.any():
        b.warnings.append(f"{b.file}: {int(bad.sum())} row(s) with unreadable '{tcol}' skipped "
                          f"(rows {', '.join(map(str, df.loc[bad, '_row']))}).")
    df = df.loc[~bad].sort_values("_ts", kind="stable").reset_index(drop=True)
    df["_t"] = (df["_ts"] - pd.Timestamp(config.INCIDENT_REFERENCE_TIME)).dt.total_seconds().round(3)
    if len(df):
        b.coverage += [float(df["_t"].min()), float(df["_t"].max())]
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
    """A declared vehicle (by id or alias) if the case lists one; otherwise the unit's own id."""
    raw = raw.strip()
    for v in config.CASE_VEHICLES:
        if raw == v["id"]:
            return v["id"], v.get("description") or None
        if raw in v.get("aliases", []):
            b.warnings.append(f"{b.file}: unit '{raw}' is declared vehicle {v['id']}.")
            return v["id"], v.get("description") or f"unit {raw}"
    if raw:
        vid = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)
        if config.CASE_VEHICLES:
            b.warnings.append(f"{b.file}: unit '{raw}' is not a declared vehicle; kept as its own id.")
        return vid, None
    vid = re.sub(r"[^A-Za-z0-9]+", "_", Path(b.file).stem)
    b.warnings.append(f"{b.file}: no vehicle id column; vehicle named '{vid}' after the file.")
    return vid, None


def _junction() -> tuple[float, float, float] | None:
    """(centre x, centre y, half-size) of the junction box, only when the case declares a junction."""
    scene = config.CASE_SCENE or {}
    if scene.get("type") != "junction":
        return None
    cx, cy = scene.get("junction_center_m") or (0.0, 0.0)
    half = scene.get("junction_radius_m") or (scene.get("lane_width_m") or 3.5)
    return float(cx), float(cy), float(half)


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
    junction = _junction()

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

        if junction and None not in (xs[i], ys[i], xs[i - 1], ys[i - 1]):
            jx, jy, half = junction
            inside = abs(xs[i] - jx) <= half and abs(ys[i] - jy) <= half
            was_inside = abs(xs[i - 1] - jx) <= half and abs(ys[i - 1] - jy) <= half
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

    # The whole measured path, for trajectories and cross-vehicle checks (contact, closest approach).
    if cols["x"] and cols["y"]:
        pts = [[round(t[i], 3), _round(xs[i]), _round(ys[i]), _round(spd[i], 1), _round(hdg[i], 1)]
               for i in range(len(t)) if _round(xs[i]) is not None and _round(ys[i]) is not None]
        if len(pts) >= 2:
            stride = max(1, len(pts) // 2000)
            pts = pts[::stride] + ([pts[-1]] if (len(pts) - 1) % stride else [])
            b.add(b.next_id(f"{prefix}_path", 2), _row_locator(row[0], row[-1]), ts[0], t[0], "track",
                  vehicle_id=vid, description=desc, confidence=0.98,
                  attributes={"units": "m", "points": pts, "point_fields": ["t", "x_m", "y_m", "speed_kmh", "heading_deg"]})


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
                  attributes={"view": int(m.group(1)) if (m := re.fullmatch(r"view_?(\d+)", view)) else view,
                              "flow_stat": col.split("_optical_flow")[0].rsplit("_", 1)[-1],
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


EARTH_R = 6_371_000.0


def latlon_to_m(lat: float, lon: float, origin: tuple[float, float]) -> tuple[float, float]:
    """Local east/north metres from an origin (equirectangular; fine over a few km)."""
    lat0, lon0 = origin
    x = math.radians(lon - lon0) * EARTH_R * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_R
    return x, y


def _extract_tracks(df: pd.DataFrame, b: _Builder) -> None:
    cols = list(df.columns)
    sp = speed_column(cols)
    scol, factor, unit, assumed = sp if sp else (None, 1.0, "kmh", False)
    picked = {"x": _pick(cols, X_COLS), "y": _pick(cols, Y_COLS), "hdg": _pick(cols, HEADING_COLS),
              "brake": _pick(cols, BRAKE_COLS)}
    lat, lon = _pick(cols, LAT_COLS), _pick(cols, LON_COLS)
    df = _with_time(df, b)
    for key, out in (("x", "_x"), ("y", "_y"), ("hdg", "_hdg")):
        if picked[key]:
            df[out] = pd.to_numeric(df[picked[key]], errors="coerce")
    if not (picked["x"] and picked["y"]) and lat and lon:
        la, lo = pd.to_numeric(df[lat], errors="coerce"), pd.to_numeric(df[lon], errors="coerce")
        origin = config.GPS_ORIGIN or (float(la.min()), float(lo.min()))
        xy = [latlon_to_m(a, o, origin) if pd.notna(a) and pd.notna(o) else (np.nan, np.nan) for a, o in zip(la, lo)]
        df["_x"], df["_y"] = [p[0] for p in xy], [p[1] for p in xy]
        picked["x"] = picked["y"] = "gps"
        b.warnings.append(f"{b.file}: GPS converted to local metres (east, north) from origin "
                          f"{origin[0]:.6f}, {origin[1]:.6f}.")
    if scol:
        df["_speed_raw"] = pd.to_numeric(df[scol], errors="coerce")
        df["_speed"] = df["_speed_raw"] * factor
        bad = df["_speed"].isna()
        if bad.any():
            b.warnings.append(f"{b.file}: {int(bad.sum())} row(s) with unreadable '{scol}' skipped.")
            df = df.loc[~bad]
        if unit != "kmh":
            b.warnings.append(f"{b.file}: speed converted from {unit} to km/h (raw value kept as speed_{unit}_raw).")
        if assumed:
            b.warnings.append(f"{b.file}: column '{scol}' has no unit in its name; read as km/h (assumed). "
                              "Check against the source before relying on speeds.")
    if picked["brake"]:
        df["_brake"] = df[picked["brake"]].str.strip().str.lower().isin(BRAKE_TRUE)

    vcol = vehicle_column(cols)
    groups = df.groupby(df[vcol].str.strip(), sort=False) if vcol else [("", df)]
    for raw_id, track in groups:
        if len(track) == 0:
            continue
        track = track.reset_index(drop=True)
        if not scol or not picked["hdg"]:
            _motion_from_positions(track, b, need_speed=not scol, need_heading=not picked["hdg"], picked=picked)
            if "_speed" not in track:
                continue
        vid, desc = _resolve_vehicle(str(raw_id), b)
        _extract_track(track, b, vid, desc, unit, picked)


def _motion_from_positions(track: pd.DataFrame, b: _Builder, need_speed: bool, need_heading: bool, picked: dict) -> None:
    """Speed (km/h) and heading (deg from north, clockwise) from successive positions when not recorded."""
    if "_x" not in track or "_y" not in track:
        if need_speed:
            b.warnings.append(f"{b.file}: no speed column and no positions; track skipped.")
        return
    t = track["_t"]
    vx = (track["_x"].shift(-1) - track["_x"].shift(1)) / (t.shift(-1) - t.shift(1))
    vy = (track["_y"].shift(-1) - track["_y"].shift(1)) / (t.shift(-1) - t.shift(1))
    vx = vx.fillna(track["_x"].diff() / t.diff()).bfill().ffill()
    vy = vy.fillna(track["_y"].diff() / t.diff()).bfill().ffill()
    if need_speed:
        track["_speed"] = (np.hypot(vx, vy) * 3.6).astype(float)
        track["_speed_raw"] = track["_speed"]
        b.warnings.append(f"{b.file}: no speed column; speed computed from positions.")
    if need_heading:
        moving = np.hypot(vx, vy) > 0.5
        track["_hdg"] = pd.Series(np.where(moving, (np.degrees(np.arctan2(vx, vy)) + 360) % 360, np.nan)).ffill().bfill()
        picked["hdg"] = "computed"


LAYOUTS = {"signal_log": "traffic-signal log", "vehicle_track": "vehicle track (telemetry/OBD)",
           "optical_flow": "optical-flow motion per camera view", "frame_index": "video frame index"}


def classify_columns(cols: list[str], df: pd.DataFrame | None = None) -> str | None:
    """Which supported CSV layout these (lower-cased) headers are, or None. With the data, time columns
    are also recognised by name pattern + values (e.g. 'ts_unix' holding epoch seconds)."""
    has_time = (time_column(df) is not None) if df is not None else _pick(cols, TIME_COLS) is not None
    if not has_time:
        return None
    if _pick(cols, APPROACH_COLS) and _pick(cols, STATE_COLS):
        return "signal_log"
    has_xy = (_pick(cols, X_COLS) and _pick(cols, Y_COLS)) or (_pick(cols, LAT_COLS) and _pick(cols, LON_COLS))
    if speed_column(cols) or has_xy:
        return "vehicle_track"
    if _flow_columns(cols):
        return "optical_flow"
    if _pick(cols, FRAME_COLS):
        return "frame_index"
    return None


def unrecognised(file: str, cols: list[str]) -> str:
    return (f"{file}: unrecognised sensor CSV headers {cols}. Expected a time column "
            f"({', '.join(TIME_COLS)}) plus speed, signal state, optical-flow or frame columns.")


def inspect_csv(path: str, filename: str) -> dict:
    """Upload-time check: layout, row count and time span, without extracting. Raises ValueError."""
    df = pd.read_csv(path, dtype=str, skipinitialspace=True, keep_default_na=False)
    cols = [str(c).strip().lower() for c in df.columns]
    if df.empty:
        raise ValueError(f"{filename}: no data rows")
    df.columns = cols
    layout = classify_columns(cols, df)
    if layout is None:
        raise ValueError(unrecognised(filename, cols))
    return {"layout": layout, "layout_name": LAYOUTS[layout], "rows": len(df), "columns": cols, **_expected_columns(cols, df)}


def _expected_columns(cols: list[str], df: pd.DataFrame) -> dict:
    """Which of the expected columns (a timestamp, a speed) the upload has. A time column is already required by
    the layout check; a missing speed is a warning, not an error, since signal logs and position-only GPS are valid."""
    speed = next((c for c in cols if re.search(r"(^|_)(speed|velocity|spd|kmh|kph|mph)($|_)", c)), None)
    time_col = time_column(df)
    found = {"timestamp": time_col[0] if time_col else None, "speed": speed}
    notes = [] if speed else ["no speed column: speeds will not be reported from this file (positions and events still are)"]
    return {"columns_found": found, "upload_warnings": notes}


def extract_sensor(evidence: Evidence) -> ObservationSet:
    """Parse timestamps, normalise speed units, flag missing records."""
    df, file = _read(evidence)
    b = _Builder(evidence, file)
    cols = list(df.columns)
    layout = classify_columns(cols, df)
    if layout == "signal_log":
        _extract_signals(df, b)
    elif layout == "vehicle_track":
        _extract_tracks(df, b)
    elif layout == "optical_flow":
        _extract_motion(df, b, _flow_columns(cols))
    elif layout == "frame_index":
        _describe_frame_index(df, b, _pick(cols, FRAME_COLS))
    else:
        raise ValueError(unrecognised(file, cols[:-1]))

    b.observations.sort(key=lambda o: (o.t_rel_s if o.t_rel_s is not None else float("inf")))
    return ObservationSet(source_type=SourceType.sensor, evidence_id=evidence.id, producer=PRODUCER,
                          generated_at=datetime.now(timezone.utc), observations=b.observations,
                          warnings=b.warnings,
                          coverage_s=(min(b.coverage), max(b.coverage)) if b.coverage else None)


def scan_csv(path: str) -> dict:
    """Cheap pre-pass for the pipeline: earliest absolute timestamp and smallest lat/lon, if any."""
    try:
        df = pd.read_csv(path, dtype=str, skipinitialspace=True, keep_default_na=False)
    except Exception:
        return {}
    df.columns = [str(c).strip().lower() for c in df.columns]
    cols = list(df.columns)
    out: dict = {}
    found = time_column(df)
    tcol, kind = found if found else (None, None)
    if tcol and kind != "rel":
        if kind == "iso":
            ts = pd.to_datetime(df[tcol], format="ISO8601", utc=True, errors="coerce")
        else:
            ts = pd.to_datetime(pd.to_numeric(df[tcol], errors="coerce"), unit="ms" if kind == "ms" else "s",
                                utc=True, errors="coerce")
        ts = ts.dropna()
        if len(ts):
            # keep the file's own offset when it has one, so the report shows local clock time
            first = df[tcol][ts.index[ts.argmin()]]
            parsed = pd.Timestamp(first) if kind == "iso" else ts.min()
            out["earliest"] = (parsed if parsed.tzinfo else parsed.tz_localize("UTC")).to_pydatetime()
    lat, lon = _pick(cols, LAT_COLS), _pick(cols, LON_COLS)
    if lat and lon:
        la, lo = pd.to_numeric(df[lat], errors="coerce").dropna(), pd.to_numeric(df[lon], errors="coerce").dropna()
        if len(la) and len(lo):
            out["latlon"] = (float(la.min()), float(lo.min()))
    return out
