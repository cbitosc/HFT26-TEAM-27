"""False contradictions found while preparing the pitch: a witness's local clock time compared with a UTC clock, and a
speed the model attached to a sentence that gives none. Both produced high-severity conflicts that were not there."""
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, pipeline
from app.main import app
from app.modules.extraction.sensor import scan_csv
from app.modules.extraction.witness import speed_is_stated
from app.schemas import Evidence, SourceType
from test_extraction import _mock_witness

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))


# ------------------------------------------------------------------ a speed must be a number the witness said

@pytest.mark.parametrize("speed,text,expected", [
    (80, "I would say at least 80 km/h", True), (40, "it was doing about forty", True), (20, "maybe 20", True),
    (80, "about 50 mph", True), (54, "roughly 15 m/s", True),                              # the same speed in another unit
    (60, "The red car sped up to beat the light, it was going fast", False),               # a number from nowhere
    (70, "really fast, much too fast. The crash was at quarter to six", False)])
def test_a_speed_is_the_witnesss_only_if_a_matching_number_is_stated(speed, text, expected):
    assert speed_is_stated(speed, text) is expected


def test_an_invented_speed_is_dropped_and_said_so(monkeypatch, tmp_path):
    text = "The red car sped up, it was going fast.\nThe SUV came at least 80 km/h.\n"
    reply = {"observations": [
        {"line": 1, "raw_text": "The red car sped up, it was going fast", "vehicle_description": "red car", "action": "accelerating",
         "attributes": {"speed_kmh": 60, "speed_qualifier": "about"}, "certainty": "certain"},
        {"line": 2, "raw_text": "The SUV came at least 80 km/h", "vehicle_description": "SUV", "action": "moving",
         "attributes": {"speed_kmh": 80, "speed_qualifier": "at least"}, "certainty": "fairly_sure"}]}
    out, _ = _mock_witness(monkeypatch, tmp_path, text, [reply])
    fast, stated = out.observations
    assert "speed_kmh" not in fast.attributes and "speed_qualifier" not in fast.attributes
    assert stated.attributes["speed_kmh"] == 80.0 and stated.attributes["speed_qualifier"] == "at least"
    assert any("speed 60 km/h dropped" in w for w in out.warnings)


def test_a_real_speed_the_model_attached_to_the_next_sentence_is_kept(monkeypatch, tmp_path):
    """The model often puts "at least 80 km/h" on the following claim ("it did not slow down"). The witness did say it."""
    text = "A big white SUV came from my left really fast, I would say at least 80 km/h.\nIt did not slow down at all.\n"
    reply = {"observations": [
        {"line": 2, "raw_text": "It did not slow down at all", "vehicle_description": "white SUV", "action": "no_braking",
         "attributes": {"speed_kmh": 80, "speed_qualifier": "at least"}, "certainty": "fairly_sure"}]}
    out, _ = _mock_witness(monkeypatch, tmp_path, text, [reply])
    (o,) = out.observations
    assert o.attributes["speed_kmh"] == 80.0 and not any("dropped" in w for w in out.warnings)


# ------------------------------------------------------------------ the clock a witness's time is compared with

def _clock_reply(text):
    return {"observations": [{"line": 1, "raw_text": text.strip(), "vehicle_description": None, "action": "collision",
                              "clock_time": "09:15", "time_uncertainty_min": 15, "certainty": "fairly_sure"}]}


def test_a_local_clock_time_is_compared_when_the_case_declared_its_clock(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "INCIDENT_REFERENCE_TIME", datetime(2026, 9, 3, 9, 14, 30, tzinfo=IST))
    monkeypatch.setattr(config, "REFERENCE_SOURCE", "case")
    out, _ = _mock_witness(monkeypatch, tmp_path, "It was around quarter past nine.\n", [_clock_reply("It was around quarter past nine.")])
    (o,) = out.observations
    assert o.timestamp == datetime(2026, 9, 3, 9, 15, tzinfo=IST) and o.time_uncertainty_s == 900 and "clock_zone" not in o.attributes


def test_a_local_clock_time_is_not_compared_with_a_utc_clock(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "INCIDENT_REFERENCE_TIME", datetime(2026, 9, 3, 3, 44, 30, tzinfo=timezone.utc))
    monkeypatch.setattr(config, "REFERENCE_SOURCE", "evidence")
    out, _ = _mock_witness(monkeypatch, tmp_path, "It was around quarter past nine.\n", [_clock_reply("It was around quarter past nine.")])
    (o,) = out.observations
    assert o.timestamp is None and o.attributes["clock_time"] == "09:15" and o.attributes["clock_zone"] == "unknown"
    assert any("clock zone is unknown" in w for w in out.warnings)


def test_a_placeholder_reference_time_is_not_a_clock_either(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REFERENCE_SOURCE", "default")
    out, _ = _mock_witness(monkeypatch, tmp_path, "It was around quarter past nine.\n", [_clock_reply("It was around quarter past nine.")])
    assert out.observations[0].timestamp is None


# ------------------------------------------------------------------ the evidence's own zone is used when it states one

def _csv(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("\n".join(rows) + "\n")
    return p


def test_scan_reports_the_files_own_offset_but_not_utc(tmp_path):
    iso = _csv(tmp_path, "a.csv", ["time,speed_kmh", "2026-09-03T09:14:30+05:30,40", "2026-09-03T09:14:31+05:30,41"])
    zulu = _csv(tmp_path, "b.csv", ["time,speed_kmh", "2026-09-03T03:44:30Z,40", "2026-09-03T03:44:31Z,41"])
    epoch = _csv(tmp_path, "c.csv", ["ts_unix,speed_kmh", "1788407070.0,40", "1788407071.0,41"])
    assert scan_csv(str(iso))["zone"] == timedelta(hours=5, minutes=30)
    assert "zone" not in scan_csv(str(zulu)) and "zone" not in scan_csv(str(epoch))


def _evidence(path, kind=SourceType.sensor):
    return Evidence(id=f"ev_{path.stem}", kind=kind, filename=path.name, size_bytes=1, stored_path=str(path),
                    uploaded_at=datetime.now(timezone.utc))


def test_the_reference_clock_takes_the_zone_another_file_states(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REFERENCE_SOURCE", "default")
    epoch = _csv(tmp_path, "lead.csv", ["ts_unix,device,lat,lon", "1788407070.0,G1,17.44,78.34", "1788407071.0,G1,17.45,78.35"])
    iso = _csv(tmp_path, "truck.csv", ["time,unit_id,latitude,longitude", "2026-09-03T09:14:30+05:30,T1,17.44,78.34", "2026-09-03T09:14:31+05:30,T1,17.45,78.35"])
    pipeline._prepare_run_frame([_evidence(epoch), _evidence(iso)])
    ref = config.INCIDENT_REFERENCE_TIME
    assert ref == datetime(2026, 9, 3, 3, 44, 30, tzinfo=timezone.utc)          # the same instant ...
    assert ref.utcoffset() == timedelta(hours=5, minutes=30)                     # ... shown in the scene's zone
    assert config.REFERENCE_SOURCE == "evidence" and "+5:30" in config.REFERENCE_DETAIL


def test_epoch_only_evidence_stays_utc(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REFERENCE_SOURCE", "default")
    epoch = _csv(tmp_path, "lead.csv", ["ts_unix,device,lat,lon", "1788407070.0,G1,17.44,78.34", "1788407071.0,G1,17.45,78.35"])
    pipeline._prepare_run_frame([_evidence(epoch)])
    assert config.INCIDENT_REFERENCE_TIME.utcoffset() == timedelta(0)


# ------------------------------------------------------------------ end to end: the case that produced the false conflict

def test_a_correct_local_time_witness_no_longer_contradicts_the_gps_record(monkeypatch):
    case = ROOT / "eval" / "cases" / "gps_rear_end"
    from app.modules.extraction import witness as wmod

    def fake_chat(messages):  # what a model returns for the auto driver's statement: one clock time, nothing else
        return json.dumps({"observations": [{"line": 2, "raw_text": "It was around quarter past nine in the morning.", "vehicle_description": None,
                                             "action": "collision", "clock_time": "09:15", "time_uncertainty_min": 15, "certainty": "fairly_sure"}]})

    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "k")
    monkeypatch.setattr(wmod, "_chat", fake_chat)
    client = TestClient(app)
    for f in ("lead_gps.csv", "truck_gps.csv"):
        assert client.post("/api/evidence/sensor", files={"file": (f, (case / "evidence" / f).read_bytes())}).status_code == 201
    assert client.post("/api/evidence/witness", files={"file": ("witness_auto_driver.txt", (case / "evidence" / "witness_auto_driver.txt").read_bytes())}).status_code == 201
    client.post("/api/analysis/run")
    kinds = [c["type"] for c in client.get("/api/conflicts").json()["conflicts"]]
    assert "timestamp_overlap" not in kinds
    wit = [o for s in client.get("/api/observations").json() for o in s["observations"] if o["action"] == "collision" and o["source_type"] == "witness"]
    assert wit and wit[0]["timestamp"].startswith("2026-09-03T09:15:00") and wit[0]["timestamp"].endswith("+05:30")
