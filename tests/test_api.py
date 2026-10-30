"""Walking skeleton: API serves fixtures end to end, before any module is built."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_results_served_before_any_run():
    assert len(client.get("/api/observations").json()) == 3
    assert client.get("/api/timeline").json()["events"]
    assert client.get("/api/conflicts").json()["conflicts"]
    assert client.get("/api/reconstruction").json()["vehicles"]
    assert client.get("/api/analysis/latest").json() is None


def test_demo_run_without_evidence_uses_fixtures():
    run = client.post("/api/analysis/run").json()
    assert run["evidence_count"] == 0
    # no uploads: extractors serve their demo fixtures; modules 3-5 run for real on them
    assert {s["status"] for s in run["stages"][:3]} == {"fixture"}
    assert [s["status"] for s in run["stages"][3:]] == ["real", "real", "real"]
    assert len(run["stages"]) == 6
    assert client.get("/api/analysis/latest").json()["run_id"] == run["run_id"]


def test_upload_list_delete_and_run():
    r = client.post("/api/evidence", files={"file": ("w.txt", b"I saw a red car.", "text/plain")})
    assert r.status_code == 201 and r.json()["kind"] == "witness"
    ev_id = r.json()["id"]
    client.post("/api/evidence", files={"file": ("s.csv", b"a,b\n1,2\n", "text/csv")})
    assert len(client.get("/api/evidence").json()) == 2

    run = client.post("/api/analysis/run").json()
    assert run["evidence_count"] == 2
    # one observation set per source type actually uploaded (fixture-backed for now)
    kinds = sorted(s["source_type"] for s in client.get("/api/observations").json())
    assert kinds == ["sensor", "witness"]

    assert client.delete(f"/api/evidence/{ev_id}").status_code == 204
    assert client.delete(f"/api/evidence/{ev_id}").status_code == 404


def test_upload_rejects_unknown_type():
    r = client.post("/api/evidence", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415


def test_real_sensor_evidence_flows_through_modules_2_to_5():
    """Integration: generated sensor CSVs -> real extraction -> real timeline, conflicts, reconstruction."""
    from pathlib import Path

    data = Path(__file__).resolve().parents[1] / "data" / "sensor"
    for name in ("v1_telematics.csv", "v2_obd.csv", "signal_controller.csv"):
        r = client.post("/api/evidence", files={"file": (name, (data / name).read_bytes(), "text/csv")})
        assert r.status_code == 201

    run = client.post("/api/analysis/run").json()
    statuses = {s["stage"].split(" [")[0]: s["status"] for s in run["stages"]}
    assert "error" not in statuses.values(), run["stages"]
    assert all(statuses[k] == "real" for k in ("2. extraction.sensor", "3. timeline", "4. contradictions", "5. reconstruction"))

    sets = client.get("/api/observations").json()
    assert {s["source_type"] for s in sets} == {"sensor"}
    assert any(o["action"] == "sensor_gap" for s in sets for o in s["observations"])

    timeline = client.get("/api/timeline").json()
    entering = {e["vehicle_ids"][0] for e in timeline["events"] if "enters the junction" in e["label"] and abs(e["t_start_s"] - 10.0) <= 0.5}
    assert entering == {"V1", "V2"}
    conflicts = client.get("/api/conflicts").json()["conflicts"]
    assert any(c["type"] == "missing_evidence" and set(c["t_range_s"] or []) == {6.0, 7.5} for c in conflicts)
    single = sorted(c["title"] for c in conflicts if c["title"].endswith("has a single source"))
    # Only the phases each vehicle entered on are decisive (V2 on red, V1 on yellow).
    assert single == ["'Eastbound signal red' has a single source", "'Northbound signal yellow' has a single source"]

    # Sensors alone carry no "collision" observation, so no marker is expected; tracks must still meet.
    recon = client.get("/api/reconstruction").json()
    at_10 = {v["vehicle_id"]: next(p for p in v["track"] if p["t_s"] == 10.0) for v in recon["vehicles"]}
    assert set(at_10) == {"V1", "V2"}
    for p in at_10.values():
        assert abs(p["x_m"] + 1.75) <= 1.0 and abs(p["y_m"] - 1.75) <= 1.0
