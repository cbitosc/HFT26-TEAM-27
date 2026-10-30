"""Module 5 end to end: upload evidence -> run -> reconstruction -> "id and positions" JSON.

Oracle: scripts/scenario.py. Uses the real sensor extractors (no key), so positions, the collision marker and the
replay window come from the whole pipeline, not from a fixture.
"""
import json
import math
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import scenario as sc  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    return TestClient(app)


def _run_demo(client, files=("signal_controller.csv", "v1_telematics.csv", "v2_obd.csv")):
    assert client.put("/api/incident", json=json.loads((ROOT / "data" / "case.json").read_text())).status_code == 200
    for f in files:
        r = client.post("/api/evidence", files={"file": (f, (ROOT / "data" / "sensor" / f).read_bytes())})
        assert r.status_code == 201, r.text
    run = client.post("/api/analysis/run").json()
    assert {s["stage"]: s["status"] for s in run["stages"]}["5. reconstruction"] == "real"
    return run


def test_positions_json_matches_ground_truth(client):
    _run_demo(client)
    r = client.get("/api/reconstruction/positions")
    assert r.status_code == 200
    out = r.json()
    assert out["coordinate_system"]["units"] == "m" and out["t_min_s"] < sc.COLLISION_T < out["t_max_s"]
    assert {v["id"] for v in out["vehicles"]} == {"V1", "V2"}
    for v in out["vehicles"]:
        pos = v["positions"]
        assert [p["t_s"] for p in pos] == sorted(p["t_s"] for p in pos) and len(pos) > 10
        assert all({"t_s", "x", "y", "basis", "confidence"} <= set(p) for p in pos)
        veh = sc.V1 if v["id"] == "V1" else sc.V2
        for p in pos:  # positions follow the ground-truth path to within a metre
            truth = sc.state_at(veh, p["t_s"])
            assert math.dist((p["x"], p["y"]), (truth["x"], truth["y"])) < 1.0, (v["id"], p["t_s"])
    c = out["collision"]
    assert abs(c["t_s"] - sc.COLLISION_T) <= 0.5 and math.dist((c["x"], c["y"]), sc.COLLISION_XY) < 2.0
    assert c["supporting_observation_ids"]


def test_download_header_and_positions_are_what_the_map_shows(client):
    _run_demo(client)
    r = client.get("/api/reconstruction/positions?download=true")
    assert "vehicle_positions.json" in r.headers["content-disposition"]
    rec = client.get("/api/reconstruction").json()
    for v in rec["vehicles"]:
        mine = next(x for x in r.json()["vehicles"] if x["id"] == v["vehicle_id"])
        assert [(p["t_s"], p["x_m"], p["y_m"]) for p in v["track"]] == [(p["t_s"], p["x"], p["y"]) for p in mine["positions"]]


def test_replay_window_is_continuous_for_the_slider(client):
    _run_demo(client)
    rec = client.get("/api/reconstruction").json()
    for v in rec["vehicles"]:
        ts = [p["t_s"] for p in v["track"]]
        assert ts[0] == rec["t_min_s"] and ts[-1] == rec["t_max_s"]
        assert all(abs((b - a) - 0.5) < 1e-6 for a, b in zip(ts, ts[1:]))  # every slider step has a position
        assert all(p["source"] in {"measured", "interpolated", "extrapolated", "estimated", "tracked"} for p in v["track"])


def test_one_vehicle_only_has_no_collision_marker_without_evidence(client):
    _run_demo(client, files=("v2_obd.csv",))
    out = client.get("/api/reconstruction/positions").json()
    assert [v["id"] for v in out["vehicles"]] == ["V2"]
    assert out["collision"] is None


def test_no_evidence_gives_empty_export_with_note(client):
    client.put("/api/incident", json=json.loads((ROOT / "data" / "case.json").read_text()))
    assert client.post("/api/analysis/run").status_code in (200, 409, 422)
    out = client.get("/api/reconstruction/positions").json()
    assert isinstance(out["vehicles"], list)


def test_switching_case_can_clear_the_reference_time(client):
    """A new case without a declared clock must not inherit the previous case's t = 0."""
    assert client.put("/api/incident", json={"reference_time": "2026-10-12T17:42:00+05:30"}).json()["reference_time_confirmed"]
    assert client.put("/api/incident", json={"title": "x"}).json()["reference_time_confirmed"]  # untouched when absent
    assert not client.put("/api/incident", json={"reference_time": None}).json()["reference_time_confirmed"]
