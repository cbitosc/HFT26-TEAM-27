"""Score the pipeline on every case in eval/cases against its truth.json. The guard against overfitting:
a change must hold up on all cases, not just the one being looked at.

    .venv/bin/python scripts/evaluate.py              # all cases (uses the Featherless key if .env has one)
    .venv/bin/python scripts/evaluate.py gps_rear_end --no-llm

Per case it checks: expected events (action, vehicle, time within tolerance), the collision (time and
vehicles, or that none is claimed), expected contradictions (type + key words), camera calibration and
speeds. Checks that need witness extraction are skipped without an LLM key. Writes eval/results.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "eval" / "cases"


def _load_env(use_llm: bool) -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    if not use_llm:
        os.environ["FEATHERLESS_API_KEY"] = ""


def run_case(client, config, case_dir: Path) -> dict:
    # fresh storage and case settings for every case
    storage = Path(tempfile.mkdtemp(prefix=f"eval-{case_dir.name}-"))
    config.STORAGE_DIR, config.UPLOAD_DIR, config.RUN_DIR = storage, storage / "uploads", storage / "runs" / "latest"
    config.INCIDENT_ID, config.INCIDENT_TITLE, config.INCIDENT_LOCATION = "CASE", "", ""
    config.REFERENCE_TIME_CONFIRMED, config.REFERENCE_SOURCE, config.REFERENCE_DETAIL = False, "default", ""
    config.CASE_VEHICLES, config.CASE_SCENE, config.LANE_MARKINGS, config.GPS_ORIGIN = [], {"type": "unknown"}, None, None
    case = json.loads((case_dir / "case.json").read_text())
    r = client.put("/api/incident", json=case)
    assert r.status_code == 200, r.text
    for f in sorted((case_dir / "evidence").iterdir()):
        r = client.post("/api/evidence", files={"file": (f.name, f.read_bytes())})
        assert r.status_code == 201, f"{f.name}: {r.text}"
    run = client.post("/api/analysis/run").json()
    get = lambda name: client.get(f"/api/{name}").json()  # noqa: E731
    return {"run": run, "timeline": get("timeline"), "conflicts": get("conflicts")["conflicts"],
            "reconstruction": get("reconstruction"),
            "observations": [o for s in get("observations") for o in s["observations"]],
            "warnings": [w for s in get("observations") for w in s["warnings"]]}


def plan_shape_error(plan_truth: dict, out: dict):
    """Shape error of the schematic plan against a known camera pose: every plan point is matched to the true ground
    position of the vehicle it was tracked on (by projecting the truth into the image), then the RMS relative error of
    pairwise distances is taken after the best single scale (the plan has no metres, so scale is free)."""
    import itertools
    import numpy as np
    sys.path.insert(0, str(ROOT / "scripts"))
    from synthetic import camera_pose

    p = plan_truth["pose"]
    project, _ = camera_pose(p["w"], p["h"], p["focal"], p["cam_height"], p["pitch_deg"], p["yaw_deg"])
    obs = {o["id"]: o for o in out["observations"]}
    truth, plan = [], []
    for v in out["reconstruction"]["vehicles"]:
        if not v["vehicle_id"].startswith("T"):
            continue  # only measured motion tracks test the geometry; AI-sighted (B...) tracks are coarse boxes
        src = obs.get(v["source_observation_ids"][0]) if v["source_observation_ids"] else None
        if not src or len(src["attributes"].get("points", [])) != len(v["track"]):
            continue
        for (t, u, w), tp in zip(src["attributes"]["points"], v["track"]):
            best = None
            for veh in plan_truth["vehicles"]:
                z = veh["z0"] + veh["speed_ms"] * t
                pr = project(veh["lane_x"], 0.0, z)
                if pr is not None and (best is None or np.hypot(pr[0] - u, pr[1] - w) < best[0]):
                    best = (float(np.hypot(pr[0] - u, pr[1] - w)), veh["lane_x"], z)
            if best and best[0] < 25:
                truth.append(np.array([best[1], best[2]]))
                plan.append(np.array([tp["x_m"], tp["y_m"]]))
    if len(truth) < 3:
        return None, len(truth)
    dt = np.array([np.hypot(*(truth[i] - truth[j])) for i, j in itertools.combinations(range(len(truth)), 2)])
    dp = np.array([np.hypot(*(plan[i] - plan[j])) for i, j in itertools.combinations(range(len(plan)), 2)])
    keep = dt > 0.5
    dt, dp = dt[keep], dp[keep]
    s = float(dt @ dp) / float(dp @ dp)
    return float(np.sqrt(np.mean(((s * dp - dt) / dt) ** 2))) * 100, len(truth)


def score(truth: dict, out: dict, llm: bool) -> list[dict]:
    checks = []
    obs = {o["id"]: o for o in out["observations"]}
    witness_ran = any(o["source_type"] == "witness" for o in out["observations"])

    def add(kind, desc, ok, detail="", skipped=False):
        checks.append({"kind": kind, "desc": desc, "ok": ok, "detail": detail, "skipped": skipped})

    for e in truth.get("events", []):
        hits = [ev for ev in out["timeline"]["events"]
                if any(obs.get(i, {}).get("action") == e["action"] for i in ev["observation_ids"])
                and (not e.get("vehicle") or e["vehicle"] in ev["vehicle_ids"])]
        best = min(hits, key=lambda ev: abs(ev["t_start_s"] - e["t"]), default=None)
        err = None if best is None else best["t_start_s"] - e["t"]
        add("event", e["desc"], err is not None and abs(err) <= e["tol"],
            "not found" if err is None else f"t={best['t_start_s']:g} s (error {err:+.2f} s, tol {e['tol']})")

    if "collision" in truth:
        coll = [ev for ev in out["timeline"]["events"] if ev["category"] == "collision"]
        want = truth["collision"]
        if want is None:
            add("collision", "no collision claimed", not coll, f"{len(coll)} collision event(s)")
        else:
            best = min(coll, key=lambda ev: abs(ev["t_start_s"] - want["t"]), default=None)
            ok = best is not None and abs(best["t_start_s"] - want["t"]) <= want["tol"] \
                and set(want["vehicles"]) <= set(best["vehicle_ids"])
            add("collision", f"collision at {want['t']:g} s between {', '.join(want['vehicles'])}", ok,
                "not found" if best is None else
                f"t={best['t_start_s']:g} s, vehicles {best['vehicle_ids']} (error {best['t_start_s'] - want['t']:+.2f} s)")
            if len(coll) > 1:
                add("collision", "exactly one collision event", False, f"{len(coll)} collision events")

    for c in truth.get("conflicts", []):
        if c.get("requires_llm") and not (llm and witness_ran):
            add("conflict", c["desc"], False, "needs witness extraction (LLM key)", skipped=True)
            continue
        hit = next((x for x in out["conflicts"] if x["type"] == c["type"] and all(
            k.lower() in (x["title"] + " " + x["description"] + " " + " ".join(x["observation_ids"])).lower()
            or k in [obs.get(i, {}).get("vehicle_id") for i in x["observation_ids"]] for k in c["contains"])), None)
        add("conflict", c["desc"], hit is not None, hit["title"] if hit else "not found")

    if want := truth.get("reconstruction"):
        rec = out["reconstruction"]
        if want.get("schematic"):
            speeds = [p["speed_kmh"] for v in rec["vehicles"] for p in v["track"] if p.get("speed_kmh") is not None]
            add("reconstruction", "uncalibrated camera -> labelled schematic plan, no metres, speeds or impact point",
                rec["scene"]["units"] == "schematic" and bool(rec["vehicles"]) and not speeds and rec["collision"] is None
                and "NOT to scale" in (rec.get("note") or "")
                and all(p["source"] == "estimated" for v in rec["vehicles"] for p in v["track"]),
                f"{rec['scene']['units']}, {len(rec['vehicles'])} vehicle(s)")
            if want.get("road_aligned"):
                add("reconstruction", "plan is road-aligned (direction taken from the lane lines)",
                    "direction comes from the lane lines" in (rec.get("note") or ""), "road direction taken from the picture" if "direction comes from the lane lines" in (rec.get("note") or "") else "nominal camera used")
            if want.get("max_path_angle_deg"):  # every synthetic vehicle drives along its lane: its path must run along the plan's y axis
                import math
                angles = [math.degrees(math.atan2(abs(v["track"][-1]["x_m"] - v["track"][0]["x_m"]),
                                                  abs(v["track"][-1]["y_m"] - v["track"][0]["y_m"])))
                          for v in rec["vehicles"] if v["vehicle_id"].startswith("T")]  # motion tracks only (see plan_shape_error)
                add("reconstruction", f"vehicle paths run along the road axis (within {want['max_path_angle_deg']:g} degrees)",
                    bool(angles) and max(angles) <= want["max_path_angle_deg"], ", ".join(f"{a:.1f}" for a in angles) + " degrees off")
            if want.get("plan_error_max_pct") and truth.get("plan_truth"):
                err, n = plan_shape_error(truth["plan_truth"], out)
                add("reconstruction", f"plan proportions within {want['plan_error_max_pct']:g}% of the true road plane",
                    err is not None and n >= want.get("min_points", 3) and err <= want["plan_error_max_pct"],
                    "no matchable points" if err is None else f"{err:.1f}% over {n} points")
        elif want.get("empty"):
            add("reconstruction", "no top-down map without positional evidence", not rec["vehicles"] and bool(rec.get("note")),
                rec.get("note") or f"{len(rec['vehicles'])} vehicle(s)")
        else:
            add("reconstruction", f"{want['scene']} map with >= {want['min_vehicles']} vehicles",
                rec["scene"]["type"] == want["scene"] and len(rec["vehicles"]) >= want["min_vehicles"],
                f"{rec['scene']['type']}, {len(rec['vehicles'])} vehicle(s)")

    if "calibrated_views" in truth:
        calibrated = sorted({int(w.split()[2]) for w in out["warnings"] if w.startswith("Camera view") and " calibrated (" in w})
        add("calibration", f"calibrated views {truth['calibrated_views']}", calibrated == truth["calibrated_views"],
            f"calibrated: {calibrated}")
    tracks = [o for o in out["observations"] if o["action"] == "track" and o["attributes"].get("units") == "px"]
    for sp in truth.get("speeds", []):
        cands = [o["attributes"]["mean_speed_kmh"] for o in tracks if o["attributes"].get("mean_speed_kmh") is not None
                 and o["attributes"].get("direction") == sp["direction"]]
        best = min(cands, key=lambda v: abs(v - sp["kmh"]), default=None)
        ok = best is not None and abs(best - sp["kmh"]) / sp["kmh"] * 100 <= sp["tol_pct"]
        add("speed", f"{sp['kmh']} km/h {sp['direction']}", ok,
            "no calibrated track" if best is None else f"{best:g} km/h ({(best - sp['kmh']) / sp['kmh']:+.0%})")
    if truth.get("speeds") == [] and "calibrated_views" in truth:
        add("speed", "no km/h without a declared standard",
            not any(o["attributes"].get("mean_speed_kmh") for o in tracks), "")
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--no-llm", action="store_true", help="skip witness/vision model calls")
    args = ap.parse_args()
    _load_env(not args.no_llm)
    if not CASES.exists() or not any(CASES.iterdir()):
        sys.exit("No cases: run scripts/make_cases.py first.")
    sys.path.insert(0, str(ROOT / "src" / "backend"))
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    client = TestClient(app)
    llm = bool(os.environ.get("FEATHERLESS_API_KEY"))
    results, total, passed, skipped = {}, 0, 0, 0
    for case_dir in sorted(p for p in CASES.iterdir() if p.is_dir() and (not args.cases or p.name in args.cases)):
        truth = json.loads((case_dir / "truth.json").read_text())
        checks = score(truth, run_case(client, config, case_dir), llm)
        results[case_dir.name] = checks
        print(f"\n## {case_dir.name} — {truth.get('about', '')}")
        for c in checks:
            mark = "SKIP" if c["skipped"] else ("PASS" if c["ok"] else "FAIL")
            print(f"  [{mark}] {c['kind']:<11} {c['desc']:<45} {c['detail']}")
            total += not c["skipped"]
            passed += c["ok"] and not c["skipped"]
            skipped += c["skipped"]
    print(f"\n{passed}/{total} checks passed" + (f", {skipped} skipped (no LLM)" if skipped else ""))
    (ROOT / "eval" / "results.json").write_text(json.dumps({"llm": llm, "passed": passed, "total": total,
                                                            "skipped": skipped, "cases": results}, indent=2))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
