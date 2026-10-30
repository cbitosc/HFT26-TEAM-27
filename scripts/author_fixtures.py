"""Authoring aid for /fixtures (the hand-written module outputs).

Each observation, event and conflict below was picked by hand to tell the
scenario.py story; only the coordinates and speeds are computed. The JSON in
/fixtures is the contract artefact; real modules must never import this.

Usage: python scripts/author_fixtures.py   (overwrites /fixtures)
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import scenario as sc
FX = ROOT / "fixtures"; FX.mkdir(exist_ok=True)
GEN = "2026-10-30T00:00:00Z"
def ts(t): return (sc.T0 + timedelta(seconds=t)).isoformat()
def ob(id, st, ev, file, loc, t, act, vid=None, desc=None, attrs=None, conf=0.9, unc=0.0, raw=None, timestamp="auto"):
    return {"id": id, "source_type": st, "source_ref": {"evidence_id": ev, "file": file, "locator": loc},
            "timestamp": ts(t) if timestamp == "auto" and t is not None else (None if timestamp == "auto" else timestamp),
            "t_rel_s": t, "time_uncertainty_s": unc, "vehicle_id": vid, "vehicle_description": desc,
            "action": act, "attributes": attrs or {}, "confidence": conf, "raw_text": raw}
def dump(name, obj): (FX / name).write_text(json.dumps(obj, indent=2) + "\n"); print("wrote fixtures/" + name)

# ---- module 1: video
V = ("video", "ev_fx_dashcam", "dashcam_v1_front.mp4")
f = lambda t: f"frame:{int(round(t * 30))}"
dump("video_observations.json", {"schema_version": "1.0.0", "source_type": "video", "evidence_id": "ev_fx_dashcam",
 "producer": "fixture:evidence_ingestion", "generated_at": GEN, "warnings": [
   "Dashcam clock offset assumed 0 s relative to telematics (not verified).",
   "Eastbound signal head not visible from ego camera."],
 "observations": [
  ob("vid_001", *V, f(0.0), 0.0, "signal_state", None, None, {"approach": "NB", "signal_state": "green"}, 0.85),
  ob("vid_002", *V, f(7.2), 7.2, "signal_state", None, None, {"approach": "NB", "signal_state": "yellow"}, 0.8, 0.2),
  ob("vid_003", *V, f(9.1), 9.1, "entering_intersection", "V2", "white SUV, enters from left (west)", {"direction": "eastbound"}, 0.8, 0.1),
  ob("vid_004", *V, f(9.2), 9.2, "braking", "V1", "ego vehicle (dashcam host)", {"cue": "forward pitch of horizon"}, 0.6, 0.3),
  ob("vid_005", *V, f(10.0), 10.0, "collision", None, None, {"vehicles": ["V1", "V2"], "impact_side": "V1 front-right / V2 left side"}, 0.9, 0.05),
  ob("vid_006", *V, f(11.5), 11.5, "stopped", "V1", "ego vehicle", {}, 0.7, 0.3),
 ]})

# ---- module 2: witness
W1 = ("witness", "ev_fx_w1", "witness_01_pedestrian.txt"); W2 = ("witness", "ev_fx_w2", "witness_02_shopkeeper.txt")
dump("witness_observations.json", {"schema_version": "1.0.0", "source_type": "witness", "evidence_id": "ev_fx_witness_bundle",
 "producer": "fixture:extraction.witness", "generated_at": GEN, "warnings": [
   "W1 and W2 give no absolute times for individual actions; ordering is relative.",
   "W2 vehicle type for V2 ambiguous (SUV or van)."],
 "observations": [
  ob("wit_w1_01", *W1, "line:7", None, "collision", None, None, {"vehicles": ["V1", "V2"], "clock_time": "17:40"}, 0.5, 300, "around 5:40 in the evening", timestamp="2026-10-12T17:40:00+05:30"),
  ob("wit_w1_02", *W1, "line:10", None, "signal_state", "V1", "red sedan", {"approach": "NB", "signal_state": "green", "order": 1}, 0.6, raw="its light was green when it was approaching"),
  ob("wit_w1_03", *W1, "line:11", None, "moving", "V2", "big white SUV", {"direction": "eastbound", "speed_kmh": 80, "speed_qualifier": "at least", "order": 2}, 0.5, raw="really fast, I would say at least 80 km/h"),
  ob("wit_w1_04", *W1, "line:11", None, "no_braking", "V2", "white SUV", {"order": 3}, 0.6, raw="The SUV did not slow down at all"),
  ob("wit_w1_05", *W1, "line:12", None, "braking", "V1", "red car", {"order": 4, "cue": "heard brakes"}, 0.7, raw="The red car tried to stop, I heard the brakes"),
  ob("wit_w1_06", *W1, "line:13", None, "collision", None, None, {"vehicles": ["V1", "V2"], "location": "middle of the junction", "order": 5}, 0.85, raw="they hit in the middle of the junction"),
  ob("wit_w1_07", *W1, "line:13", None, "post_collision_slide", None, None, {"vehicles": ["V1", "V2"], "distance_m_est": 3, "order": 6}, 0.6, raw="Both cars slid a few metres"),
  ob("wit_w2_01", *W2, "line:7", None, "collision", None, None, {"vehicles": ["V1", "V2"], "clock_time": "17:45"}, 0.5, 120, "at about quarter to six", timestamp="2026-10-12T17:45:00+05:30"),
  ob("wit_w2_02", *W2, "line:8", None, "signal_state", "V1", "red car", {"approach": "NB", "signal_state": "yellow", "order": 1}, 0.7, raw="The light for Main Road had already turned yellow"),
  ob("wit_w2_03", *W2, "line:8", None, "accelerating", "V1", "red car", {"order": 2, "claim": "sped up to beat the light"}, 0.6, raw="The red car sped up to beat the light"),
  ob("wit_w2_04", *W2, "line:9", None, "moving", "V2", "white SUV or van", {"direction": "eastbound", "vehicle_type_alternatives": ["SUV", "van"]}, 0.5, raw="I think it was an SUV or maybe a van"),
  ob("wit_w2_05", *W2, "line:11", None, "entering_intersection", "V1", "red car", {"order": 3, "relative": "before V2"}, 0.65, raw="The red car went into the junction first"),
 ]})

# ---- module 2: sensor
def row_v1(t):  # header row 1, gap rows removed
    times = [i * 0.5 for i in range(27) if not (sc.V1_SENSOR_GAP[0] < i * 0.5 < sc.V1_SENSOR_GAP[1])]
    return f"row:{times.index(t) + 2}"
S1 = ("sensor", "ev_fx_v1tel", "v1_telematics.csv"); S2 = ("sensor", "ev_fx_v2obd", "v2_obd.csv"); SG = ("sensor", "ev_fx_signal", "signal_controller.csv")
def kmh(v, t): return round(sc.state_at(v, t)["speed"] * 3.6, 1)
def pos(v, t): s = sc.state_at(v, t); return {"x_m": round(s["x"], 2), "y_m": round(s["y"], 2), "heading_deg": s["heading"]}
dump("sensor_observations.json", {"schema_version": "1.0.0", "source_type": "sensor", "evidence_id": "ev_fx_sensor_bundle",
 "producer": "fixture:extraction.sensor", "generated_at": GEN, "warnings": [
   "v1_telematics.csv: no records between t=6.0 s and t=7.5 s (2 samples missing at 2 Hz).",
   "v2_obd.csv: speed converted from mph to km/h; unit_id OBD-7731 mapped to V2."],
 "observations": [
  ob("sen_v1_001", *S1, row_v1(0.0), 0.0, "moving", "V1", "red sedan, TS09 AB 1234", {"speed_kmh": kmh(sc.V1, 0), **pos(sc.V1, 0)}, 0.98),
  ob("sen_v1_gap", *S1, "rows:14-15", 6.0, "sensor_gap", "V1", None, {"gap_start_s": 6.0, "gap_end_s": 7.5, "missing_samples": 2}, 1.0),
  ob("sen_v1_002", *S1, row_v1(9.0), 9.0, "braking", "V1", None, {"speed_kmh": kmh(sc.V1, 9.0), **pos(sc.V1, 9.0)}, 0.97, 0.5),
  ob("sen_v1_003", *S1, row_v1(10.0), 10.0, "entering_intersection", "V1", None, {"speed_kmh": kmh(sc.V1, 10.0), **pos(sc.V1, 10.0)}, 0.95),
  ob("sen_v1_004", *S1, row_v1(10.5), 10.5, "sudden_heading_change", "V1", None, {"heading_change_deg": 35, "speed_kmh": kmh(sc.V1, 10.5), **pos(sc.V1, 10.5)}, 0.9, 0.5),
  ob("sen_v1_005", *S1, row_v1(11.5), 11.5, "stopped", "V1", None, pos(sc.V1, 11.5), 0.95),
  ob("sen_v2_001", *S2, "row:2", 0.0, "moving", "V2", "unit OBD-7731", {"speed_kmh": kmh(sc.V2, 0), "speed_mph_raw": 35.8, **pos(sc.V2, 0)}, 0.98),
  ob("sen_v2_002", *S2, "row:22", 10.0, "braking", "V2", None, {"speed_kmh": kmh(sc.V2, 10.0), "brake_onset_between_s": [9.5, 10.0], **pos(sc.V2, 10.0)}, 0.95, 0.5),
  ob("sen_v2_003", *S2, "row:23", 10.5, "sudden_heading_change", "V2", None, {"heading_change_deg": -15, **pos(sc.V2, 10.5)}, 0.9, 0.5),
  ob("sen_v2_004", *S2, "row:25", 11.5, "stopped", "V2", None, pos(sc.V2, 11.5), 0.95),
  ob("sig_001", *SG, "row:4", 7.0, "signal_state", None, None, {"approach": "NB", "signal_state": "yellow"}, 1.0),
  ob("sig_002", *SG, "row:5", 10.0, "signal_state", None, None, {"approach": "NB", "signal_state": "red"}, 1.0),
  ob("sig_000", *SG, "row:2", -20.0, "signal_state", None, None, {"approach": "EB", "signal_state": "red"}, 1.0),
  ob("sig_003", *SG, "row:6", 11.0, "signal_state", None, None, {"approach": "EB", "signal_state": "green"}, 1.0),
 ]})

# ---- module 3: timeline
def ev(id, t0, t1, label, cat, vids, oids, conf, unc=0.0):
    sts = sorted({ {"vid": "video", "wit": "witness", "sen": "sensor", "sig": "sensor"}[o.split("_")[0]] for o in oids })
    return {"id": id, "t_start_s": t0, "t_end_s": t1, "timestamp": ts(t0), "label": label, "category": cat,
            "vehicle_ids": vids, "observation_ids": oids, "source_types": sts, "confidence": conf, "uncertainty_s": unc}
dump("timeline.json", {"schema_version": "1.0.0", "incident_id": sc.INCIDENT_ID, "reference_time": sc.T0.isoformat(), "generated_at": GEN, "events": [
  ev("evt_01", -20.0, 11.0, "Eastbound (5th Ave) signal red", "signal", [], ["sig_000"], 1.0),
  ev("evt_02", 0.0, 9.0, "V1 red sedan northbound on Main Rd at ~47 km/h", "vehicle_motion", ["V1"], ["sen_v1_001", "vid_001"], 0.95),
  ev("evt_03", 0.0, 9.6, "V2 white SUV eastbound on 5th Ave at ~58 km/h", "vehicle_motion", ["V2"], ["sen_v2_001", "wit_w1_03", "wit_w2_04"], 0.9),
  ev("evt_04", 7.0, 10.0, "Northbound signal yellow", "signal", [], ["sig_001", "vid_002", "wit_w2_02"], 0.95, 0.2),
  ev("evt_05", 9.0, 9.2, "V1 begins braking", "vehicle_motion", ["V1"], ["sen_v1_002", "vid_004", "wit_w1_05"], 0.9, 0.5),
  ev("evt_06", 9.1, 9.1, "V2 enters junction from the west against red", "vehicle_motion", ["V2"], ["vid_003", "sig_000"], 0.8, 0.1),
  ev("evt_07", 9.5, 10.0, "V2 begins braking (late)", "vehicle_motion", ["V2"], ["sen_v2_002"], 0.75, 0.5),
  ev("evt_08", 10.0, 10.0, "Collision in north-west quadrant of junction", "collision", ["V1", "V2"],
     ["vid_005", "sen_v1_003", "sen_v1_004", "sen_v2_002", "sen_v2_003", "wit_w1_06", "wit_w2_05", "wit_w1_01", "wit_w2_01"], 0.95, 0.1),
  ev("evt_09", 10.0, 10.0, "Northbound signal turns red", "signal", [], ["sig_002"], 1.0),
  ev("evt_10", 10.0, 11.5, "Both vehicles slide north-east and stop", "post_collision", ["V1", "V2"], ["sen_v1_005", "sen_v2_004", "vid_006", "wit_w1_07"], 0.85, 0.3),
  ev("evt_11", 11.0, 11.0, "Eastbound signal turns green (after collision)", "signal", [], ["sig_003"], 1.0),
 ]})

# ---- module 4: conflicts
def c(id, typ, sev, title, desc, oids, tr=None, details=None):
    return {"id": id, "type": typ, "severity": sev, "title": title, "description": desc, "observation_ids": oids,
            "t_range_s": tr, "details": details or {}}
dump("conflicts.json", {"schema_version": "1.0.0", "incident_id": sc.INCIDENT_ID, "generated_at": GEN, "conflicts": [
  c("cf_01", "numeric_mismatch", "high", "V2 speed overstated by witness W1",
    "W1 estimates the SUV at 'at least 80 km/h'; V2 OBD records a steady 57.6 km/h before braking.",
    ["wit_w1_03", "sen_v2_001"], (0.0, 9.6), {"claimed_kmh": 80, "measured_kmh": 57.6, "delta_kmh": 22.4, "rule": "|claimed - measured| > 15 km/h"}),
  c("cf_02", "semantic", "high", "Northbound signal state disputed",
    "W1 says V1's light was green on approach. The signal controller and dashcam both show yellow from t≈7 s; V1 entered at t≈10 s. W2 agrees it was yellow.",
    ["wit_w1_02", "sig_001", "vid_002", "wit_w2_02"], (7.0, 10.0), {"method": "llm_semantic", "agreeing": ["sig_001", "vid_002", "wit_w2_02"]}),
  c("cf_03", "semantic", "medium", "W2 claims V1 accelerated; telemetry shows braking",
    "W2 says the red car 'sped up to beat the light'. V1 telematics shows constant 46.8 km/h, then braking from t=9.0 s.",
    ["wit_w2_03", "sen_v1_001", "sen_v1_002"], (7.0, 10.0), {"method": "rule:speed_trend", "measured_trend": "constant_then_decreasing"}),
  c("cf_04", "timestamp_overlap", "medium", "W2's incident time does not overlap sensor time",
    "W2 puts the crash at about 17:45 (±2 min); sensors place the collision at 17:42:10. W1's ~17:40 (±5 min) is consistent.",
    ["wit_w2_01", "sen_v1_003", "wit_w1_01"], (10.0, 10.0), {"claimed_window": ["17:43:00", "17:47:00"], "sensor_time": "17:42:10", "offset_s": 170}),
  c("cf_05", "missing_evidence", "medium", "V1 telematics dropout during yellow onset",
    "No V1 records between t=6.0 s and t=7.5 s, exactly when the northbound signal changed to yellow. V1's reaction to the yellow cannot be confirmed from telemetry.",
    ["sen_v1_gap", "sig_001"], (6.0, 7.5), {"missing_samples": 2, "sample_rate_hz": 2}),
  c("cf_06", "missing_evidence", "low", "No direct view of V2's signal",
    "V2's red-light entry is inferred from the controller log only. The dashcam cannot see the eastbound signal and W2 did not see it.",
    ["sig_000", "vid_003"], (9.0, 10.0), {}),
  c("cf_07", "semantic", "low", "V2 vehicle type ambiguous",
    "W2 is unsure whether V2 was an SUV or a van; dashcam and W1 say SUV.",
    ["wit_w2_04", "vid_003", "wit_w1_03"], None, {}),
 ]})

# ---- module 5: reconstruction (predefined coordinates from scenario.py)
RECON_T_MIN = 5.0  # vehicles are >120 m out before this; keeps the view tight
times = [RECON_T_MIN + i * 0.5 for i in range(int((sc.T_END - RECON_T_MIN) * 2) + 1)]
def track(v):
    out = []
    for t in times:
        s = sc.state_at(v, t)
        out.append({"t_s": t, "x_m": round(s["x"], 2), "y_m": round(s["y"], 2), "heading_deg": s["heading"], "speed_kmh": round(s["speed"] * 3.6, 1)})
    return out
dump("reconstruction.json", {"schema_version": "1.0.0", "incident_id": sc.INCIDENT_ID, "generated_at": GEN,
  "scene": {"type": "four_way_intersection", "extent_m": 60, "lane_width_m": 3.5, "roads": {"ns": "Main Rd", "ew": "5th Ave"}},
  "t_min_s": RECON_T_MIN, "t_max_s": sc.T_END,
  "vehicles": [
    {"vehicle_id": "V1", "label": "V1 · red sedan", "color": "#dc2626", "description": sc.V1["description"], "track": track(sc.V1),
     "source_observation_ids": ["sen_v1_001", "sen_v1_002", "sen_v1_003", "sen_v1_005"]},
    {"vehicle_id": "V2", "label": "V2 · white SUV", "color": "#64748b", "description": sc.V2["description"], "track": track(sc.V2),
     "source_observation_ids": ["sen_v2_001", "sen_v2_002", "sen_v2_004", "vid_003"]},
  ],
  "collision": {"t_s": sc.COLLISION_T, "x_m": sc.COLLISION_XY[0], "y_m": sc.COLLISION_XY[1], "confidence": 0.9,
                "supporting_observation_ids": ["vid_005", "sen_v1_003", "sen_v2_002", "wit_w1_06"]},
})
