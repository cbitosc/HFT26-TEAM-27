"""Generate the demo sensor CSVs into data/sensor/.

Three deliberately different formats so the sensor extractor has real
normalisation work to do:

* v1_telematics.csv   ISO timestamps, km/h, 2 Hz, with a dropout gap
* v2_obd.csv          epoch milliseconds, mph, 2 Hz
* signal_controller.csv  phase-change log for the junction

Usage: python scripts/generate_sensor_data.py
"""
import csv
from datetime import timedelta
from pathlib import Path

import scenario as sc

OUT = Path(__file__).resolve().parents[1] / "data" / "sensor"
STEP = 0.5
MS_TO_KMH = 3.6
MS_TO_MPH = 2.236936


def _times(start: float = 0.0, end: float = sc.T_END):
    n = int(round((end - start) / STEP))
    return [round(start + i * STEP, 3) for i in range(n + 1)]


def write_v1() -> Path:
    path = OUT / "v1_telematics.csv"
    gap_lo, gap_hi = sc.V1_SENSOR_GAP
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "vehicle_id", "speed_kmh", "x_m", "y_m", "heading_deg", "brake"])
        for t in _times():
            if gap_lo < t < gap_hi:
                continue  # simulated telematics dropout
            s = sc.state_at(sc.V1, t)
            ts = (sc.T0 + timedelta(seconds=t)).isoformat().replace("+00:00", "Z")
            w.writerow([ts, "V1", f"{s['speed'] * MS_TO_KMH:.1f}", f"{s['x']:.2f}", f"{s['y']:.2f}",
                        f"{s['heading']:.0f}", int(s["braking"])])
    return path


def write_v2() -> Path:
    path = OUT / "v2_obd.csv"
    t0_ms = int(sc.T0.timestamp() * 1000)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch_ms", "unit_id", "speed_mph", "pos_x", "pos_y", "hdg", "brake_pedal"])
        for t in _times():
            s = sc.state_at(sc.V2, t)
            w.writerow([t0_ms + int(t * 1000), "OBD-7731", f"{s['speed'] * MS_TO_MPH:.1f}",
                        f"{s['x']:.2f}", f"{s['y']:.2f}", f"{s['heading']:.0f}",
                        "ON" if s["braking"] else "OFF"])
    return path


def write_signals() -> Path:
    path = OUT / "signal_controller.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "approach", "state"])
        for t_start, _t_end, approach, state in sorted(sc.SIGNAL_PHASES, key=lambda p: (p[0], p[2])):
            ts = (sc.T0 + timedelta(seconds=t_start)).isoformat().replace("+00:00", "Z")
            w.writerow([ts, approach, state])
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for p in (write_v1(), write_v2(), write_signals()):
        print(f"wrote {p.relative_to(OUT.parents[1])}")


def write_case() -> Path:
    """The demo's case file: what an investigator would declare (vehicles, junction, clock)."""
    import json
    path = OUT.parent / "case.json"
    path.write_text(json.dumps({
        "incident_id": sc.INCIDENT_ID,
        "title": "Red sedan / white SUV junction collision (demo)",
        "location": sc.LOCATION,
        "reference_time": sc.T0.isoformat(),
        "vehicles": [
            {"id": "V1", "description": sc.V1["description"], "aliases": []},
            {"id": "V2", "description": sc.V2["description"], "aliases": ["OBD-7731"]},
        ],
        "scene": {"type": "junction", "traffic_side": "left", "roads": {"ns": "Main Rd", "ew": "5th Ave"},
                  "lane_width_m": 3.5, "junction_center_m": [0, 0], "junction_radius_m": 3.5},
    }, indent=2) + "\n")
    return path


if __name__ == "__main__":
    print(f"wrote {write_case().relative_to(OUT.parents[1])}")
