"""Gaps 4-6: per-kind uploads with validation and EVID ids, evidence_ref on observations, traceable positions."""
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def client(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    return TestClient(app)


def _file(kind, name):
    return {"file": (name, (ROOT / "data" / kind / name).read_bytes())}


# ------------------------------------------------------------------ gap 4

def test_witness_and_sensor_endpoints_store_with_evid_ids(client):
    a = client.post("/api/evidence/sensor", files=_file("sensor", "v2_obd.csv"))
    b = client.post("/api/evidence/witness", files=_file("witness", "witness_01_pedestrian.txt"))
    assert a.status_code == b.status_code == 201
    assert (a.json()["ref"], b.json()["ref"]) == ("EVID-001", "EVID-002")
    assert a.json()["kind"] == "sensor" and b.json()["kind"] == "witness"
    # the original is kept byte for byte
    stored = Path(b.json()["stored_path"]).read_bytes()
    assert stored == (ROOT / "data" / "witness" / "witness_01_pedestrian.txt").read_bytes()


def test_sensor_csv_report_which_expected_columns_it_has(client):
    d = client.post("/api/evidence/sensor", files=_file("sensor", "v2_obd.csv")).json()["details"]
    assert d["columns_found"]["timestamp"] == "epoch_ms" and d["columns_found"]["speed"] == "speed_mph"
    assert d["upload_warnings"] == []
    no_speed = b"timestamp,pos_x,pos_y\n2026-10-12T17:42:00Z,0,0\n2026-10-12T17:42:01Z,5,0\n"
    r = client.post("/api/evidence/sensor", files={"file": ("gps.csv", no_speed)})
    assert r.status_code == 201
    assert r.json()["details"]["columns_found"]["speed"] is None
    assert "no speed column" in r.json()["details"]["upload_warnings"][0]


def test_validation_rejects_unusable_files_with_a_reason(client):
    assert client.post("/api/evidence/witness", files={"file": ("w.txt", b"   \n  ")}).status_code in (400, 422)
    assert client.post("/api/evidence/witness", files={"file": ("w.txt", b"\xff\xfe\x00bad")}).status_code == 422
    no_time = client.post("/api/evidence/sensor", files={"file": ("s.csv", b"speed,x\n1,2\n3,4\n")})
    assert no_time.status_code == 422 and "time" in no_time.json()["detail"].lower()
    empty_csv = client.post("/api/evidence/sensor", files={"file": ("s.csv", b"timestamp,speed\n")})
    assert empty_csv.status_code == 422
    # right content, wrong door
    wrong = client.post("/api/evidence/sensor", files=_file("witness", "witness_01_pedestrian.txt"))
    assert wrong.status_code == 415 and "witness" in wrong.json()["detail"]
    assert client.get("/api/evidence").json() == []  # nothing rejected was stored


def test_evid_numbers_are_never_reused(client):
    first = client.post("/api/evidence/sensor", files=_file("sensor", "v2_obd.csv")).json()
    second = client.post("/api/evidence/sensor", files=_file("sensor", "v1_telematics.csv")).json()
    assert client.delete(f"/api/evidence/{second['id']}").status_code == 204
    third = client.post("/api/evidence/witness", files=_file("witness", "witness_01_pedestrian.txt")).json()
    assert [first["ref"], second["ref"], third["ref"]] == ["EVID-001", "EVID-002", "EVID-003"]


def test_evidence_without_a_ref_is_numbered_by_upload_order(client, tmp_path):
    from app import config, store
    client.post("/api/evidence/sensor", files=_file("sensor", "v2_obd.csv"))
    client.post("/api/evidence/sensor", files=_file("sensor", "v1_telematics.csv"))
    idx = config.STORAGE_DIR / "evidence_index.json"
    items = json.loads(idx.read_text())
    for e in items:
        e.pop("ref")
    idx.write_text(json.dumps(items))
    (config.STORAGE_DIR / "evidence_counter.txt").unlink()
    assert [e.ref for e in store.list_evidence()] == ["EVID-001", "EVID-002"]


# ------------------------------------------------------------------ gap 5

def test_observations_carry_a_readable_evidence_ref(client):
    client.put("/api/incident", json=json.loads((ROOT / "data" / "case.json").read_text()))
    client.post("/api/evidence/sensor", files=_file("sensor", "signal_controller.csv"))
    s = client.post("/api/evidence/sensor", files=_file("sensor", "v1_telematics.csv")).json()
    client.post("/api/analysis/run")
    obs = [o for st in client.get("/api/observations").json() for o in st["observations"]
           if o["source_ref"]["evidence_id"] == s["id"]]
    assert obs and all(o["evidence_ref"].startswith(s["ref"] + ", ") for o in obs)
    assert any(re.fullmatch(rf"{s['ref']}, row \d+", o["evidence_ref"]) for o in obs)
    gap = next(o for o in obs if o["action"] == "sensor_gap")  # "EVID-002, rows 14-15": the dropout, traceable to its rows
    assert re.fullmatch(rf"{s['ref']}, rows \d+-\d+", gap["evidence_ref"])


def test_witness_ref_points_at_the_line_and_keeps_the_quote():
    from datetime import datetime, timezone
    from app.evidence_refs import stamp_evidence_refs
    from app.schemas import Evidence, Observation, ObservationSet, SourceRef
    ev = Evidence(id="ev_abc", ref="EVID-002", kind="witness", filename="w.txt", size_bytes=1, stored_path="x",
                  uploaded_at=datetime.now(timezone.utc))
    o = Observation(id="w1", source_type="witness", source_ref=SourceRef(evidence_id="ev_abc", file="w.txt", locator="line:3"),
                    action="braking", confidence=0.7, raw_text="he braked hard")
    st = ObservationSet(source_type="witness", evidence_id="ev_abc", producer="t", generated_at=datetime.now(timezone.utc), observations=[o])
    stamp_evidence_refs([st], [ev])
    assert o.evidence_ref == "EVID-002, line 3" and o.raw_text == "he braked hard"


def test_demo_fixture_refs_keep_their_own_ids(client):
    from app.evidence_refs import locator_text
    assert locator_text("line:3") == "line 3" and locator_text("rows:412-415") == "rows 412-415"
    sets = client.get("/api/observations").json()  # no run yet: fixtures, which carry no evidence_ref
    assert sets and all(o.get("evidence_ref") is None for s in sets for o in s["observations"])


# ------------------------------------------------------------------ gap 6

def test_every_position_names_its_basis_and_observations(client):
    client.put("/api/incident", json=json.loads((ROOT / "data" / "case.json").read_text()))
    for f in ("signal_controller.csv", "v1_telematics.csv", "v2_obd.csv"):
        client.post("/api/evidence/sensor", files=_file("sensor", f))
    client.post("/api/analysis/run")
    ids = {o["id"] for s in client.get("/api/observations").json() for o in s["observations"]}
    export = client.get("/api/reconstruction/positions").json()
    positions = [p for v in export["vehicles"] for p in v["positions"]]
    assert positions
    for p in positions:
        assert p["basis"] in {"sensor", "video", "simulated"}
        assert p["observation_ids"], p
        assert set(p["observation_ids"]) <= ids
        assert p["source"] in {"measured", "interpolated", "extrapolated", "tracked", "estimated"}
    assert {p["basis"] for p in positions} == {"sensor"}
    # a measured keyframe cites its own observation; a point between two samples cites both neighbours
    v = export["vehicles"][0]["positions"]
    assert next(p for p in v if p["source"] == "measured")["observation_ids"]
    filled = [p for p in v if p["source"] == "interpolated"]  # V1's telemetry drops out at 6.0-7.5 s
    assert filled and all("sen_v1_gap_01" in p["observation_ids"] for p in filled)


def test_bundled_demo_positions_are_labelled_simulated(client):
    export = client.get("/api/reconstruction/positions").json()  # no evidence: demo fixture
    assert {p["basis"] for v in export["vehicles"] for p in v["positions"]} == {"simulated"}


def test_camera_tracks_are_video_basis():
    from app.modules.reconstruction import positions_export
    from app.schemas import (Observation, ObservationSet, Reconstruction, Scene, SourceRef, TrackPoint, VehicleTrack)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    obs = Observation(id="vid_x_trk_001", source_type="video", source_ref=SourceRef(evidence_id="ev_real", file="a.mp4", locator="frames:1-9"),
                      action="track", confidence=0.6)
    sets = [ObservationSet(source_type="video", evidence_id="ev_real", producer="t", generated_at=now, observations=[obs])]
    rec = Reconstruction(incident_id="X", generated_at=now, scene=Scene(type="metric", units="schematic", extent_m=10), t_min_s=0, t_max_s=1,
                         vehicles=[VehicleTrack(vehicle_id="T1", label="T1", color="#0f0", track=[TrackPoint(t_s=0, x_m=0, y_m=0, heading_deg=0, source="estimated", confidence=0.25)],
                                                source_observation_ids=["vid_x_trk_001"])])
    p = positions_export(rec, sets)["vehicles"][0]["positions"][0]
    assert p["basis"] == "video" and p["observation_ids"] == ["vid_x_trk_001"] and p["source"] == "estimated"
