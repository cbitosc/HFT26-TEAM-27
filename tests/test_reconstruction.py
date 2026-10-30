"""Module 5: trajectory reconstruction on the fixture observation sets.

Oracle: scripts/scenario.py (collision at t=10.0 s at (-1.75, 1.75)).
"""
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import store
from app.modules.reconstruction import build_reconstruction
from app.schemas import ConflictReport, Observation, ObservationSet, Reconstruction, SourceRef, Timeline

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scenario  # noqa: E402

OBS = ["video_observations.json", "witness_observations.json", "sensor_observations.json"]
COLLISION_XY = scenario.COLLISION_XY


def _inputs():
    sets = [store.load_fixture(n, ObservationSet) for n in OBS]
    return sets, store.load_fixture("timeline.json", Timeline), store.load_fixture("conflicts.json", ConflictReport)


def _at(rec: Reconstruction, vid: str, t: float):
    track = next(v for v in rec.vehicles if v.vehicle_id == vid).track
    return next(p for p in track if abs(p.t_s - t) < 1e-6)


@pytest.fixture
def rec() -> Reconstruction:
    return build_reconstruction(*_inputs())


def test_window_and_scene(rec):
    assert rec.t_min_s == pytest.approx(5.0)
    assert rec.t_max_s == pytest.approx(13.0)
    assert rec.scene.extent_m == 60 and rec.scene.lane_width_m == 3.5
    assert rec.scene.roads == {"ns": "Main Rd", "ew": "5th Ave"}


def test_tracks_cover_window_and_are_monotonic(rec):
    assert {v.vehicle_id for v in rec.vehicles} == {"V1", "V2"}
    for v in rec.vehicles:
        ts = [p.t_s for p in v.track]
        assert ts[0] == pytest.approx(rec.t_min_s) and ts[-1] == pytest.approx(rec.t_max_s)
        assert all(b - a == pytest.approx(0.5) for a, b in zip(ts, ts[1:]))


@pytest.mark.parametrize("vid", ["V1", "V2"])
def test_vehicles_meet_at_collision_point(rec, vid):
    p = _at(rec, vid, 10.0)
    assert math.dist((p.x_m, p.y_m), COLLISION_XY) < 1.0


@pytest.mark.parametrize("vid,truth", [("V1", scenario.V1), ("V2", scenario.V2)])
def test_pre_impact_tracks_match_ground_truth(rec, vid, truth):
    for t in [5.0, 6.5, 7.0, 8.0, 9.5]:  # includes V1's 6.0-7.5 s sensor gap
        p, s = _at(rec, vid, t), scenario.state_at(truth, t)
        assert math.dist((p.x_m, p.y_m), (s["x"], s["y"])) < 0.5, t
        assert p.speed_kmh == pytest.approx(s["speed"] * 3.6, abs=1.0)


def test_collision_marker(rec):
    c = rec.collision
    assert c is not None
    assert c.t_s == pytest.approx(10.0, abs=0.1)
    assert math.dist((c.x_m, c.y_m), COLLISION_XY) < 1.0
    assert "vid_005" in c.supporting_observation_ids


def test_measured_points_are_listed(rec):
    v1 = next(v for v in rec.vehicles if v.vehicle_id == "V1")
    assert {"sen_v1_002", "sen_v1_003"} <= set(v1.source_observation_ids)
    assert "sen_v1_gap" not in v1.source_observation_ids  # no position, not a measured point


def test_labels_and_colours(rec):
    by_id = {v.vehicle_id: v for v in rec.vehicles}
    assert by_id["V1"].label == "V1 · red sedan"
    assert by_id["V2"].label == "V2 · white SUV"


def test_no_marker_without_collision_event():
    sets, timeline, conflicts = _inputs()
    timeline = timeline.model_copy(update={"events": [e for e in timeline.events if e.category != "collision"]})
    assert build_reconstruction(sets, timeline, conflicts).collision is None


def test_no_marker_when_collision_event_is_unsupported():
    sets, timeline, conflicts = _inputs()
    events = [e.model_copy(update={"observation_ids": ["does_not_exist"]}) if e.category == "collision" else e
              for e in timeline.events]
    assert build_reconstruction(sets, timeline.model_copy(update={"events": events}), conflicts).collision is None


def test_approach_defaults_without_positions():
    """With no sensor positions at all, V1/V2 still drive their lanes into the conflict point."""
    sets, timeline, conflicts = _inputs()
    sets = [s for s in sets if s.source_type.value != "sensor"]
    rec = build_reconstruction(sets, timeline, conflicts)
    assert {v.vehicle_id for v in rec.vehicles} == {"V1", "V2"}
    v1_early, v2_early = _at(rec, "V1", 7.0), _at(rec, "V2", 7.0)
    assert v1_early.x_m == pytest.approx(-1.75) and v1_early.y_m < -10 and v1_early.heading_deg == 0
    assert v2_early.y_m == pytest.approx(1.75) and v2_early.x_m < -10 and v2_early.heading_deg == 90
    assert math.dist((rec.collision.x_m, rec.collision.y_m), COLLISION_XY) < 1.0


def test_dense_sensor_track_with_gap():
    """Real telematics are 0.5 s samples with a hole at 6.0-7.5 s; the gap is bridged at constant velocity."""
    sets, timeline, conflicts = _inputs()
    obs = []
    for vid, truth in [("V1", scenario.V1), ("V2", scenario.V2)]:
        for i in range(27):
            t = i * 0.5
            if vid == "V1" and scenario.V1_SENSOR_GAP[0] < t < scenario.V1_SENSOR_GAP[1]:
                continue
            s = scenario.state_at(truth, t)
            obs.append(Observation(
                id=f"d_{vid}_{i}", source_type="sensor", t_rel_s=t, vehicle_id=vid, action="moving", confidence=0.95,
                source_ref=SourceRef(evidence_id="ev", file="x.csv", locator=f"row:{i}"),
                attributes={"x_m": s["x"], "y_m": s["y"], "heading_deg": s["heading"], "speed_kmh": s["speed"] * 3.6}))
    dense = ObservationSet(source_type="sensor", evidence_id="ev", producer="test",
                           generated_at=datetime.now(timezone.utc), observations=obs)
    rec = build_reconstruction([s for s in sets if s.source_type.value != "sensor"] + [dense], timeline, conflicts)
    for vid, truth in [("V1", scenario.V1), ("V2", scenario.V2)]:
        for t in [6.5, 7.0, 10.0, 11.0, 12.0]:
            p, s = _at(rec, vid, t), scenario.state_at(truth, t)
            assert math.dist((p.x_m, p.y_m), (s["x"], s["y"])) < 0.1, (vid, t)
    assert math.dist((rec.collision.x_m, rec.collision.y_m), COLLISION_XY) < 1.0


def test_pipeline_serves_real_reconstruction(tmp_path):
    from app.pipeline import run_analysis
    run = run_analysis()
    stage = next(s for s in run.stages if s.stage.startswith("5."))
    assert stage.status == "real", stage.message
