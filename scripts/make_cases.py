"""Build the evaluation cases in eval/cases/<name>/: evidence/, case.json and truth.json.

Each case is synthetic with an exactly known answer, and the cases are deliberately unlike each other
(junction vs straight road, telemetry vs GPS, CCTV vs dashcam, Germany vs India, declared vs undeclared
settings), so a change that only helps one of them shows up as a regression in another.

Usage: python scripts/make_cases.py   (deterministic; videos are re-rendered, ~20 s)
"""
from __future__ import annotations

import csv
import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import scenario as sc
from synthetic import render_cctv, render_cctv_pose, render_dashcam

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "cases"


RECONSTRUCTION = {"junction_side_impact": {"scene": "four_way_intersection", "min_vehicles": 2},
                  "gps_rear_end": {"scene": "metric", "min_vehicles": 2},
                  "cctv_motorway_speeds": {"scene": "metric", "min_vehicles": 3},
                  "cctv_no_standard": {"schematic": True},
                  "cctv_posed_schematic": {"schematic": True, "road_aligned": True, "min_points": 12, "plan_error_max_pct": 6.0,
                                           "max_path_angle_deg": 6.0},
                  "dashcam_brake": {"empty": True}}


def _write(case: str, case_json: dict, truth: dict) -> Path:
    truth = {**truth, "reconstruction": RECONSTRUCTION[case]}
    d = OUT / case
    (d / "evidence").mkdir(parents=True, exist_ok=True)
    (d / "case.json").write_text(json.dumps(case_json, indent=2) + "\n")
    (d / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return d / "evidence"


# ------------------------------------------------------------------ 1. the demo junction

def junction_side_impact() -> None:
    ev = _write("junction_side_impact", json.loads((ROOT / "data" / "case.json").read_text()), {
        "about": "Signalised junction, side impact: V1 enters on late yellow, V2 jumps red.",
        "collision": {"t": sc.COLLISION_T, "tol": 0.6, "vehicles": ["V1", "V2"]},
        "events": [
            {"desc": "NB signal turns yellow", "action": "signal_state", "t": 7.0, "tol": 0.3},
            {"desc": "V1 brakes", "action": "braking", "vehicle": "V1", "t": sc.V1["t_brake"], "tol": 0.7},
            {"desc": "V2 brakes", "action": "braking", "vehicle": "V2", "t": sc.V2["t_brake"], "tol": 0.7},
            {"desc": "V1 enters junction", "action": "entering_intersection", "vehicle": "V1", "t": 10.0, "tol": 0.6},
        ],
        "conflicts": [
            {"desc": "W1 overstates V2 speed", "type": "numeric_mismatch", "contains": ["V2"], "requires_llm": True},
            {"desc": "W1 says V1 had green", "type": "semantic", "contains": ["signal"], "requires_llm": True},
            {"desc": "W2 says V1 sped up", "type": "semantic", "contains": ["accelerat"], "requires_llm": True},
            {"desc": "W1 says V2 never slowed", "type": "semantic", "contains": ["V2", "no braking"], "requires_llm": True},
            {"desc": "V1 telemetry gap", "type": "missing_evidence", "contains": ["V1", "dropout"]},
        ],
        "absent_conflicts": [
            {"desc": "no clock-time conflict (W2's 'about quarter to six' covers the record)", "type": "timestamp_overlap", "requires_llm": True},
            {"desc": "no speed conflict for V1 (no witness gives V1 a number)", "type": "numeric_mismatch", "contains": ["V1"], "requires_llm": True},
        ],
    })
    for f in list((ROOT / "data" / "sensor").glob("*.csv")) + list((ROOT / "data" / "witness").glob("*.txt")):
        shutil.copy(f, ev / f.name)


# ------------------------------------------------------------------ 2. GPS rear-end, India

def gps_rear_end() -> None:
    """Lead car brakes hard at t=6 s; the truck behind reacts late (t=8.4 s, gently) and hits it at ~10.6 s."""
    t0 = datetime(2026, 9, 3, 9, 14, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    lat0, lon0 = 17.4401, 78.3489      # a straight road heading north-east-ish
    head = math.radians(30)            # direction of travel, clockwise from north
    def pos(s):                        # metres along the road -> lat, lon
        dn, de = s * math.cos(head), s * math.sin(head)
        return lat0 + dn / 111_320, lon0 + de / (111_320 * math.cos(math.radians(lat0)))

    dt, n = 0.2, 60                    # 5 Hz, 12 s
    lead = {"s": 40.0, "v": 60 / 3.6}  # starts 40 m ahead
    truck = {"s": 0.0, "v": 60 / 3.6}
    rows_a, rows_b, contact = [], [], None
    for i in range(n + 1):
        t = i * dt
        if contact is None and lead["s"] - truck["s"] <= 4.5:   # bumper to bumper (reference points 4.5 m apart)
            contact = t
            lead["v"], truck["v"] = lead["v"] + 3.0, truck["v"] * 0.35  # impact: lead pushed, truck slows sharply
        rows_a.append((t, *pos(lead["s"])))
        rows_b.append((t, *pos(truck["s"]), truck["v"]))
        a_lead = -7.0 if 6.0 <= t and lead["v"] > 20 / 3.6 and contact is None else (-4.0 if contact and lead["v"] > 0 else 0)
        a_truck = (-6.0 if contact else -3.0) if (t >= 8.4 or contact) and truck["v"] > 0 else 0
        lead["v"] = max(0.0, lead["v"] + a_lead * dt)
        truck["v"] = max(0.0, truck["v"] + a_truck * dt)
        lead["s"] += lead["v"] * dt
        truck["s"] += truck["v"] * dt
    case = {"incident_id": "CASE-GPS-REAR", "title": "Rear-end collision, straight road (synthetic)",
            "location": "Old Mumbai Hwy, Hyderabad",
            "vehicles": [{"id": "A", "description": "white hatchback", "aliases": ["GPS-11"]},
                         {"id": "B", "description": "blue truck", "aliases": ["TRK-22"]}],
            "scene": {"type": "road", "traffic_side": "left"}}
    ev = _write("gps_rear_end", case, {
        "about": "GPS only, Unix seconds, no speed/heading columns in one file; t=0 from the evidence.",
        "collision": {"t": contact, "tol": 0.6, "vehicles": ["A", "B"]},
        "events": [
            {"desc": "A brakes hard", "action": "braking", "vehicle": "A", "t": 6.0, "tol": 0.6},
            {"desc": "B brakes late", "action": "braking", "vehicle": "B", "t": 8.4, "tol": 0.6},
        ],
        "conflicts": [
            {"desc": "W1 says the truck went slowly (about 20)", "type": "numeric_mismatch", "contains": ["B"],
             "requires_llm": True},
            {"desc": "W2 says the truck never braked", "type": "semantic", "contains": ["B", "no braking"], "requires_llm": True},
        ],
        "absent_conflicts": [
            {"desc": "no clock-time conflict (the witness's local time matches the record)", "type": "timestamp_overlap", "requires_llm": True},
        ],
    })
    with open(ev / "lead_gps.csv", "w", newline="") as f:     # Unix seconds, lat/lon only
        w = csv.writer(f)
        w.writerow(["ts_unix", "device", "lat", "lon"])
        for t, la, lo in rows_a:
            w.writerow([f"{t0.timestamp() + t:.1f}", "GPS-11", f"{la:.7f}", f"{lo:.7f}"])
    with open(ev / "truck_gps.csv", "w", newline="") as f:    # ISO time, speed in m/s
        w = csv.writer(f)
        w.writerow(["time", "unit_id", "latitude", "longitude", "speed_mps"])
        for t, la, lo, v in rows_b:
            w.writerow([(t0 + timedelta(seconds=t)).isoformat(), "TRK-22", f"{la:.7f}", f"{lo:.7f}", f"{v:.2f}"])
    (ev / "witness_auto_driver.txt").write_text(
        "Statement of an auto-rickshaw driver, recorded at the scene.\n"
        "It was around quarter past nine in the morning.\n"
        "The white hatchback in front suddenly braked very hard.\n"
        "The blue truck behind it was going slowly, maybe 20 km/h, but it still hit the hatchback.\n")
    (ev / "witness_shop.txt").write_text(
        "Statement of a shop owner.\n"
        "I heard a bang and looked up. The blue truck did not brake at all before it hit the small white car.\n")


# ------------------------------------------------------------------ 3/4. fixed camera, known speeds

# Vehicles pass through the near and middle field after the tracker's ~2 s warm-up (z0 < 6 m means
# "not yet in view": e.g. the 100 km/h car appears below the camera at t = 2.5 s and drives away).
CCTV_VEHICLES = [
    {"lane_x": -1.875, "z0": 6 - 100 / 3.6 * 2.5, "speed_ms": 100 / 3.6, "color": (40, 40, 200)},   # away, 100 km/h
    {"lane_x": 1.875, "z0": 6 + 80 / 3.6 * 9.5, "speed_ms": -80 / 3.6, "color": (200, 120, 40)},    # towards, 80 km/h
    {"lane_x": -5.625, "z0": 6 - 120 / 3.6 * 3.0, "speed_ms": 120 / 3.6, "color": (60, 160, 60)},   # away, 120 km/h
]


def cctv_cases() -> None:
    truth_speeds = [{"direction": "away from camera", "kmh": 100, "tol_pct": 15},
                    {"direction": "towards camera", "kmh": 80, "tol_pct": 15},
                    {"direction": "away from camera", "kmh": 120, "tol_pct": 15}]
    ev = _write("cctv_motorway_speeds", {
        "incident_id": "CASE-CCTV-SPEEDS", "title": "Motorway CCTV, three vehicles (synthetic)",
        "lane_markings": "de_motorway", "scene": {"type": "road", "traffic_side": "right"}},
        {"about": "Fixed camera, de_motorway markings declared: calibrate and measure speeds.",
         "collision": None, "calibrated_views": [1], "speeds": truth_speeds})
    render_cctv(ev / "cctv.avi", CCTV_VEHICLES, duration_s=10.0)
    ev2 = _write("cctv_no_standard", {
        "incident_id": "CASE-CCTV-NOSTD", "title": "Same footage, no marking standard declared (synthetic)"},
        {"about": "Must NOT guess a marking standard: no calibration, no km/h.",
         "collision": None, "calibrated_views": [], "speeds": []})
    shutil.copy(ev / "cctv.avi", ev2 / "cctv.avi")


# A camera that is pitched down and turned away from the road (a bridge or pole), as real CCTV is. The plan must come
# out road-aligned: lanes parallel, vehicle paths straight, proportions right. Judged against the known camera pose.
POSE = {"w": 640, "h": 360, "focal": 560.0, "cam_height": 9.0, "pitch_deg": 22.0, "yaw_deg": -20.0}
POSED_VEHICLES = [
    {"lane_x": -1.875, "z0": 22.0, "speed_ms": 9.0, "color": (40, 40, 200)},
    {"lane_x": 1.875, "z0": 90.0, "speed_ms": -12.0, "color": (200, 120, 40)},
    {"lane_x": -5.6, "z0": 30.0, "speed_ms": 11.0, "color": (60, 160, 60)},
    {"lane_x": 5.6, "z0": 105.0, "speed_ms": -14.0, "color": (200, 60, 160)},
]


def cctv_posed() -> None:
    ev = _write("cctv_posed_schematic", {
        "incident_id": "CASE-CCTV-POSED", "title": "Pole-mounted camera, road at an angle, no marking standard (synthetic)"},
        {"about": "Camera pitched and yawed: no calibration or km/h, but the schematic plan must be road-aligned "
                  "and have the road's proportions (checked against the known pose).",
         "collision": None, "calibrated_views": [], "speeds": [],
         "plan_truth": {"pose": POSE, "vehicles": POSED_VEHICLES}})
    render_cctv_pose(ev / "cctv.avi", POSED_VEHICLES, duration_s=14.0, w=POSE["w"], h=POSE["h"], focal=POSE["focal"],
                     cam_height=POSE["cam_height"], pitch_deg=POSE["pitch_deg"], yaw_deg=POSE["yaw_deg"])


# ------------------------------------------------------------------ 5. dashcam brakes

def dashcam_brake() -> None:
    def speed(t):
        return 15.0 if t < 4 else max(0.0, 15.0 - 6.0 * (t - 4))
    ev = _write("dashcam_brake", {
        "incident_id": "CASE-DASH", "title": "Dashcam car brakes to a stop (synthetic)",
        "vehicles": [{"id": "D", "description": "grey hatchback with the dashcam", "ego": True}]},
        {"about": "Dashcam ego motion; a witness claims the camera car never slowed down.",
         "collision": None,
         "events": [{"desc": "camera car brakes", "action": "braking", "vehicle": "D", "t": 4.0, "tol": 0.8},
                    {"desc": "camera car stops", "action": "stopped", "vehicle": "D", "t": 6.5, "tol": 0.8}],
         "conflicts": [{"desc": "witness: grey hatchback never slowed", "type": "semantic", "contains": ["D", "no braking"],
                        "requires_llm": True}]})
    render_dashcam(ev / "dashcam.avi", speed, duration_s=9.0)
    (ev / "witness_cyclist.txt").write_text(
        "Statement of a cyclist.\nThe grey hatchback never slowed down, it just kept driving at the same speed.\n")


if __name__ == "__main__":
    for build in (junction_side_impact, gps_rear_end, cctv_cases, cctv_posed, dashcam_brake):
        build()
    print("\n".join(sorted(str(p.relative_to(ROOT)) for p in OUT.iterdir())))
