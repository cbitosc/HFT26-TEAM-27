"""Module 2: sensor extraction on the real data/ files, witness extraction with a mocked LLM.

scripts/scenario.py is the oracle for every expected number.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import config
from app.modules.extraction import extract_sensor, extract_witness
from app.modules.extraction import witness as witness_mod
from app.schemas import Evidence, ObservationSet, SourceType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import scenario as sc  # noqa: E402


def _evidence(path: Path, kind: SourceType) -> Evidence:
    return Evidence(id=f"ev_{path.stem}", kind=kind, filename=path.name, size_bytes=path.stat().st_size,
                    stored_path=str(path), uploaded_at=datetime.now(timezone.utc))


def _sensor(name: str) -> ObservationSet:
    out = extract_sensor(_evidence(ROOT / "data" / "sensor" / name, SourceType.sensor))
    return ObservationSet.model_validate(out.model_dump(mode="json"))


def _by_action(obs_set: ObservationSet, action: str):
    return [o for o in obs_set.observations if o.action == action]


# ------------------------------------------------------------------ sensor

def test_reference_time_matches_scenario():
    assert config.INCIDENT_REFERENCE_TIME == sc.T0


def test_v1_telematics():
    s = _sensor("v1_telematics.csv")
    assert all(o.vehicle_id == "V1" and o.source_ref.locator.startswith(("row:", "rows:")) for o in s.observations)

    moving = _by_action(s, "moving")[0]
    assert moving.t_rel_s == 0.0 and moving.source_ref.locator == "row:2"
    assert moving.attributes["speed_kmh"] == pytest.approx(sc.V1["v"] * 3.6, abs=0.05)
    assert "speed_kmh_raw" not in moving.attributes  # already km/h, nothing converted

    gap = _by_action(s, "sensor_gap")
    assert len(gap) == 1
    assert (gap[0].attributes["gap_start_s"], gap[0].attributes["gap_end_s"]) == sc.V1_SENSOR_GAP
    assert gap[0].attributes["missing_samples"] == 2
    assert gap[0].source_ref.locator == "rows:14-15"
    assert any("t=6.0 s" in w and "t=7.5 s" in w for w in s.warnings)

    braking = _by_action(s, "braking")[0]
    assert braking.t_rel_s == sc.V1["t_brake"]
    assert braking.attributes["speed_kmh"] == pytest.approx(sc.V1["v"] * 3.6, abs=0.05)

    stopped = _by_action(s, "stopped")[0]
    assert sc.state_at(sc.V1, stopped.t_rel_s)["speed"] == 0.0
    assert sc.state_at(sc.V1, stopped.t_rel_s - 0.5)["speed"] > 0.0
    assert _by_action(s, "sudden_heading_change")[0].t_rel_s > sc.COLLISION_T


def test_v2_obd_mph_converted_and_unit_mapped():
    s = _sensor("v2_obd.csv")
    assert {o.vehicle_id for o in s.observations} == {"V2"}
    moving = _by_action(s, "moving")[0]
    assert moving.attributes["speed_mph_raw"] == 35.8
    assert moving.attributes["speed_kmh"] == pytest.approx(sc.V2["v"] * 3.6, abs=0.1)
    assert moving.timestamp == sc.T0
    assert any("mph" in w for w in s.warnings) and any("OBD-7731" in w for w in s.warnings)
    assert not _by_action(s, "sensor_gap")

    braking = _by_action(s, "braking")[0]
    lo, hi = braking.attributes["brake_onset_between_s"]
    assert lo <= sc.V2["t_brake"] <= hi
    for o in s.observations:
        if "speed_kmh" in o.attributes and o.action != "moving":
            truth = sc.state_at(sc.V2, o.t_rel_s)["speed"] * 3.6
            assert o.attributes["speed_kmh"] == pytest.approx(truth, abs=0.2)


def test_signal_changes_match_scenario():
    s = _sensor("signal_controller.csv")
    got = sorted((o.t_rel_s, o.attributes["approach"], o.attributes["signal_state"]) for o in s.observations)
    want = sorted((t0, appr, state) for t0, _t1, appr, state in sc.SIGNAL_PHASES)
    assert got == want
    assert all(o.action == "signal_state" and o.id.startswith("sig_") for o in s.observations)


def test_observation_ids_unique_across_sensor_files():
    ids = [o.id for name in ("v1_telematics.csv", "v2_obd.csv", "signal_controller.csv")
           for o in _sensor(name).observations]
    assert len(ids) == len(set(ids))


def test_sensor_rejects_unknown_headers(tmp_path):
    p = tmp_path / "weird.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="unrecognised"):
        extract_sensor(_evidence(p, SourceType.sensor))


def test_speed_only_file_infers_braking_from_deceleration(tmp_path):
    p = tmp_path / "plain.csv"
    p.write_text("epoch_s,speed_ms\n0,10\n1,10\n2,6\n3,0\n")
    s = extract_sensor(_evidence(p, SourceType.sensor))
    braking = _by_action(s, "braking")[0]
    assert braking.t_rel_s == 2.0 - sc.T0.timestamp() and braking.attributes["inferred_from"] == "deceleration"
    assert _by_action(s, "stopped")[0].source_ref.locator == "row:5"
    assert any("vehicle_id left null" in w for w in s.warnings)


# ----------------------------------------------------------------- witness

W1 = ROOT / "data" / "witness" / "witness_01_pedestrian.txt"

MOCK_W1 = {"observations": [
    {"line": 7, "raw_text": "around 5:40 in the evening", "vehicle_description": None, "action": "collision",
     "order": None, "clock_time": "17:40", "time_uncertainty_min": 5,
     "attributes": {"vehicles": ["red sedan", "white SUV"]}, "certainty": "unsure"},
    {"line": 9, "raw_text": "The red car, a sedan, was coming up Main Road from the south",
     "vehicle_description": "red sedan", "action": "moving", "order": 1,
     "attributes": {"direction": "northbound"}, "certainty": "certain"},
    # quote spans lines 9-10; the LLM gives a wrong line number on purpose
    {"line": 3, "raw_text": "I am fairly sure its light was green when it was approaching",
     "vehicle_description": "red car", "action": "signal_state", "order": 2,
     "attributes": {"approach": "NB", "signal_state": "green"}, "certainty": "fairly_sure"},
    {"line": 11, "raw_text": "I would say at least 80 km/h", "vehicle_description": "big white SUV",
     "action": "moving", "order": 3, "attributes": {"speed_kmh": "80", "speed_qualifier": "at least",
                                                    "direction": "eastbound"}, "certainty": "fairly_sure"},
    {"line": 12, "raw_text": "The SUV did not slow down at all", "vehicle_description": "SUV",
     "action": "no_braking", "order": 3, "certainty": "certain"},
    {"line": 13, "raw_text": "they hit in the middle of the junction", "vehicle_description": None,
     "action": "collision", "order": 5, "certainty": "certain"},
    {"line": 14, "raw_text": "The cars flew into the air", "action": "collision", "order": 6},  # hallucinated
]}


@pytest.fixture
def mock_llm(monkeypatch):
    calls = []

    def fake_chat(messages):
        calls.append(messages)
        return "```json\n" + json.dumps(MOCK_W1) + "\n```"

    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "test-key")
    monkeypatch.setattr(witness_mod, "_chat", fake_chat)
    return calls


def test_witness_without_key_falls_back(monkeypatch):
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    with pytest.raises(NotImplementedError):
        extract_witness(_evidence(W1, SourceType.witness))


def test_witness_with_mocked_llm(mock_llm):
    s = extract_witness(_evidence(W1, SourceType.witness))
    s = ObservationSet.model_validate(s.model_dump(mode="json"))
    assert s.source_type == SourceType.witness and len(mock_llm) == 1
    assert " 10| " in mock_llm[0][1]["content"]  # statement sent with line numbers

    obs = {o.raw_text: o for o in s.observations}
    assert "The cars flew into the air" not in obs and any("flew" in w for w in s.warnings)
    assert len(s.observations) == 6 and all(o.id.startswith("wit_w1_") for o in s.observations)

    text_lines = W1.read_text().splitlines()
    for o in s.observations:
        line = int(o.source_ref.locator.split(":")[1])
        assert o.raw_text.split()[0] in text_lines[line - 1]  # quote starts on its locator line

    when = obs["around 5:40 in the evening"]
    assert when.timestamp == sc.T0.replace(minute=40) and when.time_uncertainty_s == 300
    assert when.t_rel_s is None and when.attributes["vehicles"] == ["V1", "V2"]

    green = obs["I am fairly sure its light was green when it was approaching"]
    assert green.source_ref.locator == "line:9" and green.vehicle_id == "V1"
    speed = obs["I would say at least 80 km/h"]
    assert speed.vehicle_id == "V2" and speed.attributes["speed_kmh"] == 80.0
    assert obs["The SUV did not slow down at all"].vehicle_id is None  # no colour -> not unambiguous

    # hedged claims get lower confidence than plain statements
    assert when.confidence < obs["they hit in the middle of the junction"].confidence
    assert speed.confidence < obs["The red car, a sedan, was coming up Main Road from the south"].confidence

    orders = [o.attributes["order"] for o in s.observations if "order" in o.attributes]
    assert sorted(orders) == list(range(1, 6))


@pytest.mark.parametrize("desc,vid", [
    ("red sedan", "V1"), ("The red car", "V1"), ("big white SUV", "V2"),
    ("white vehicle, SUV or maybe a van", "V2"), ("SUV", None), ("red truck", None), ("red and white car", None),
])
def test_resolve_vehicle(desc, vid):
    assert witness_mod.resolve_vehicle(desc) == vid


# ---------------------------------------------------------------- video-derived sensor CSVs

def _write(tmp_path: Path, name: str, text: str) -> Evidence:
    path = tmp_path / name
    path.write_text(text)
    return _evidence(path, SourceType.sensor)


def test_frame_index_csv_yields_no_observations_but_no_error(tmp_path):
    rows = "\n".join(f"{t},{t * 12},frames/frame_{t:04d}.jpg,sample" for t in range(10))
    out = extract_sensor(_write(tmp_path, "frame_index.csv", "timestamp_s,source_frame_approx,frame_file,note\n" + rows))
    assert out.observations == []
    assert any("frame index" in w for w in out.warnings)


def test_optical_flow_csv_emits_motion_spikes_in_time_order(tmp_path):
    header = ("timestamp_s,view_1_mean_optical_flow_px_per_frame,view_1_p95_optical_flow_px_per_frame,"
              "view_2_mean_optical_flow_px_per_frame,view_2_p95_optical_flow_px_per_frame")
    lines = ["0,,,,"]  # flow needs two frames: the first row is always empty
    for t in range(1, 30):
        v1 = 40.0 if t == 20 else 10.0 + (t % 3)
        v2 = 45.0 if t == 7 else 12.0 + (t % 2)
        lines.append(f"{t},{v1 / 5},{v1},{v2 / 5},{v2}")
    lines.append("30,,,1,12")  # a real gap in view 1
    out = extract_sensor(_write(tmp_path, "visual_motion_proxy.csv", header + "\n" + "\n".join(lines)))
    ObservationSet.model_validate(out.model_dump())
    spikes = [(o.id, o.t_rel_s, o.attributes["view"]) for o in out.observations]
    assert spikes == [("sen_motion_001", 7.0, "view_2"), ("sen_motion_002", 20.0, "view_1")]
    assert all(o.action == "motion_spike" and o.vehicle_id is None and o.confidence <= 0.8 for o in out.observations)
    assert out.observations[1].source_ref.locator == "row:22"
    gap_warnings = [w for w in out.warnings if "no flow value" in w]
    assert len(gap_warnings) == 1 and "view_1" in gap_warnings[0] and "32" in gap_warnings[0]


def test_unknown_layout_still_errors_with_hint(tmp_path):
    with pytest.raises(ValueError, match="Expected a time column"):
        extract_sensor(_write(tmp_path, "mystery.csv", "timestamp_s,foo,bar\n0,1,2\n1,2,3\n"))
