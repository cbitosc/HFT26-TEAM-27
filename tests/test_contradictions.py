"""Module 4 on the fixture inputs, checked against KNOWN_DISCREPANCIES and scenario.py."""
import sys
from pathlib import Path

import pytest

from app import config, store
from app.modules.contradictions import detect_conflicts
from app.modules.timeline import build_timeline
from app.schemas import ConflictReport, ConflictType, ObservationSet, Severity, Timeline

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scenario as sc  # noqa: E402
from generate_witness_statements import KNOWN_DISCREPANCIES  # noqa: E402

import pytest as _pytest

pytestmark = _pytest.mark.usefixtures("demo_case")  # demo data: declared vehicles, junction, clock

OBS = ["video_observations.json", "witness_observations.json", "sensor_observations.json"]


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")


def _inputs(timeline_source: str = "fixture"):
    sets = [store.load_fixture(n, ObservationSet) for n in OBS]
    if timeline_source == "module3":
        return sets, build_timeline(sets)
    return sets, store.load_fixture("timeline.json", Timeline)


# Run every check on the hand-written timeline AND on module 3's real one: the two
# group observations differently, and the pipeline feeds module 4 the real one.
@pytest.fixture(params=["fixture", "module3"])
def report(request) -> ConflictReport:
    return detect_conflicts(*_inputs(request.param))


def _find(report, type_, *obs_ids):
    hits = [c for c in report.conflicts if c.type == type_ and set(obs_ids) <= set(c.observation_ids)]
    assert hits, f"no {type_.value} conflict citing {obs_ids}"
    return hits[0]


def test_report_validates_and_references_known_ids(report):
    sets, timeline = _inputs()
    known = {o.id for s in sets for o in s.observations}
    assert ConflictReport.model_validate(report.model_dump(mode="json")) == report
    assert report.incident_id == timeline.incident_id
    assert len({c.id for c in report.conflicts}) == len(report.conflicts)
    for c in report.conflicts:
        assert c.observation_ids and set(c.observation_ids) <= known, c.id
        assert c.title and c.description


def test_known_discrepancies_listed():
    # Guard: if the oracle grows, this test file needs a matching assertion.
    assert {(d["witness"], d["claim"]) for d in KNOWN_DISCREPANCIES} == {
        ("W1", "NB light green on approach"), ("W1", "V2 speed >= 80 km/h"),
        ("W2", "V1 sped up to beat the light"), ("W2", "time about 17:45")}


def test_w1_green_light_disputed(report):
    c = _find(report, ConflictType.semantic, "wit_w1_02", "sig_001")
    assert c.severity == Severity.high
    assert c.details["recorded_state"] == sc.signal_state("NB", sc.COLLISION_T - 0.1) == "yellow"
    assert {"vid_002", "wit_w2_02"} <= set(c.details["agreeing"])
    assert "wit_w2_02" not in c.observation_ids[:1]  # W2 agrees; only W1 is disputed


def test_w1_speed_overstated(report):
    c = _find(report, ConflictType.numeric_mismatch, "wit_w1_03", "sen_v2_001")
    assert c.details["measured_kmh"] == pytest.approx(sc.V2["v"] * 3.6)
    assert c.details["delta_kmh"] == pytest.approx(80 - sc.V2["v"] * 3.6, abs=0.1)
    assert c.severity == Severity.high


def test_w2_acceleration_contradicted(report):
    c = _find(report, ConflictType.semantic, "wit_w2_03", "sen_v1_002")
    assert c.details["measured_trend"] in {"constant", "decreasing", "constant_then_decreasing"}
    assert c.t_range_s[1] == pytest.approx(sc.COLLISION_T)


def test_w2_clock_time_outside_sensor_time(report):
    c = _find(report, ConflictType.timestamp_overlap, "wit_w2_01")
    assert c.details["sensor_time"] == f"{sc.T0:%H:%M}:{int(sc.COLLISION_T):02d}"
    assert c.details["offset_s"] == 170
    assert c.details["consistent"] == ["wit_w1_01"]
    # W1's ±5 min window does overlap, so it gets no conflict of its own.
    assert not [x for x in report.conflicts
                if x.type == ConflictType.timestamp_overlap and x.observation_ids[0] == "wit_w1_01"]


def test_v1_sensor_gap(report):
    c = _find(report, ConflictType.missing_evidence, "sen_v1_gap", "sig_001")
    assert c.t_range_s == sc.V1_SENSOR_GAP
    assert c.severity == Severity.medium


def test_single_source_and_vehicle_type(report):
    c = _find(report, ConflictType.missing_evidence, "sig_000", "vid_003")
    assert c.severity == Severity.low
    c = _find(report, ConflictType.semantic, "wit_w2_04", "vid_003")
    assert c.details["alternatives"] == ["suv", "van"]


def test_accurate_witness_raises_no_speed_conflict():
    sets, timeline = _inputs()
    w1 = next(o for s in sets for o in s.observations if o.id == "wit_w1_03")
    w1.attributes.update(speed_kmh=60, speed_qualifier="about")
    report = detect_conflicts(sets, timeline)
    assert not [c for c in report.conflicts if c.type == ConflictType.numeric_mismatch]


def test_llm_pass_filters_unknown_ids(monkeypatch):
    import httpx

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            content = ('{"conflicts": ['
                       '{"witness_observation_id": "wit_w2_02", "record_observation_ids": ["sig_001", "bogus"],'
                       ' "severity": "low", "title": "Yellow timing", "description": "Controller shows yellow."},'
                       '{"witness_observation_id": "made_up", "record_observation_ids": [], "title": "x"}]}')
            return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    report = detect_conflicts(*_inputs())
    llm = [c for c in report.conflicts if c.details.get("method") == "llm_semantic"]
    assert [c.observation_ids for c in llm] == [["wit_w2_02", "sig_001"]]
    assert all(c.basis == "llm" for c in llm)


def test_witness_no_braking_claim_vs_measured_braking(report):
    """W1: 'the SUV did not slow down at all' vs V2 braking on its telemetry just before impact."""
    c = _find(report, ConflictType.semantic, "wit_w1_04", "sen_v2_002")
    assert c.details["method"] == "rule:opposing_action" and c.basis == "rule"
    assert c.severity == Severity.medium  # the record is measured, not an AI reading


def test_llm_failure_falls_back_to_rules(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", boom)
    rules_only = detect_conflicts(*_inputs())
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    assert len(rules_only.conflicts) == len(detect_conflicts(*_inputs()).conflicts) == 8


def test_dashcam_ego_lane_signal_maps_to_ego_approach():
    # Real module-1 output labels the dashcam's own signal "ego lane", not "NB".
    sets, timeline = _inputs()
    for o in (o for s in sets for o in s.observations if o.id in {"vid_001", "vid_002"}):
        o.attributes["approach"] = "ego lane"
    c = _find(detect_conflicts(sets, timeline), ConflictType.semantic, "wit_w1_02", "sig_001")
    assert "vid_002" in c.details["agreeing"]


def test_single_source_flags_only_decisive_events(report):
    single = [c for c in report.conflicts if c.title.endswith("has a single source")]
    assert len(single) <= 2, [c.title for c in single]
    assert all(c.details.get("event_id") for c in single)
    assert not any("sensor data gap" in c.title or "witness claim" in c.title for c in single)
