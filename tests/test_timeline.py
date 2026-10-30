"""Module 3: timeline built from the fixture observation sets, checked against scripts/scenario.py."""
import pytest

from app import store
from app.modules.timeline import build_timeline
from app.schemas import ObservationSet, SourceType, Timeline


@pytest.fixture
def obs_sets():
    return [store.load_fixture(f"{k}_observations.json", ObservationSet) for k in ("video", "witness", "sensor")]


@pytest.fixture
def timeline(obs_sets) -> Timeline:
    return build_timeline(obs_sets)


def _find(tl, category, pred=lambda e: True):
    return [e for e in tl.events if e.category == category and pred(e)]


def test_validates_and_sorted(timeline):
    Timeline.model_validate(timeline.model_dump())
    starts = [e.t_start_s for e in timeline.events]
    assert starts == sorted(starts)
    assert all(e.t_end_s >= e.t_start_s for e in timeline.events)
    assert len({e.id for e in timeline.events}) == len(timeline.events)


def test_every_observation_referenced_once(timeline, obs_sets):
    known = {o.id for s in obs_sets for o in s.observations}
    referenced = [i for e in timeline.events for i in e.observation_ids]
    assert set(referenced) <= known
    assert set(referenced) == known  # nothing dropped
    assert len(referenced) == len(set(referenced))


def test_collision_groups_all_sources(timeline):
    [col] = _find(timeline, "collision")
    assert col.t_start_s == pytest.approx(10.0, abs=0.2)
    assert set(col.vehicle_ids) == {"V1", "V2"}
    assert set(col.source_types) == {SourceType.video, SourceType.sensor, SourceType.witness}
    # wall-clock witness claims ("around 5:40", "quarter to six") describe the collision, not its time
    assert {"vid_005", "wit_w1_06", "wit_w1_01", "wit_w2_01"} <= set(col.observation_ids)


def test_signal_events(timeline):
    def sig(label):
        [e] = _find(timeline, "signal", lambda e: e.label == label)
        return e

    nb_yellow = sig("Northbound signal yellow")
    assert nb_yellow.t_start_s == pytest.approx(7.0)
    assert nb_yellow.t_end_s == pytest.approx(10.0)
    assert {"sig_001", "vid_002", "wit_w2_02"} <= set(nb_yellow.observation_ids)
    assert sig("Northbound signal red").t_start_s == pytest.approx(10.0)
    eb_red = sig("Eastbound signal red")
    assert (eb_red.t_start_s, eb_red.t_end_s) == (pytest.approx(-20.0), pytest.approx(11.0))
    assert sig("Eastbound signal green").t_start_s == pytest.approx(11.0)


def test_braking_and_entry(timeline):
    [v1_brake] = _find(timeline, "vehicle_motion", lambda e: e.vehicle_ids == ["V1"] and e.label.endswith("begins braking"))
    assert v1_brake.t_start_s == pytest.approx(9.0, abs=0.3)
    assert {"sen_v1_002", "vid_004", "wit_w1_05"} <= set(v1_brake.observation_ids)

    [v2_brake] = _find(timeline, "vehicle_motion", lambda e: e.vehicle_ids == ["V2"] and e.label.endswith("begins braking"))
    assert (v2_brake.t_start_s, v2_brake.t_end_s) == (pytest.approx(9.5), pytest.approx(10.0))

    [v2_entry] = _find(timeline, "vehicle_motion", lambda e: e.vehicle_ids == ["V2"] and "enters" in e.label)
    assert v2_entry.t_start_s == pytest.approx(9.1)
    assert "against red" in v2_entry.label


def test_motion_and_post_collision(timeline):
    [v1_move] = _find(timeline, "vehicle_motion", lambda e: e.label.startswith("V1 red sedan"))
    assert "47 km/h" in v1_move.label and v1_move.t_end_s == pytest.approx(9.0)
    [v2_move] = _find(timeline, "vehicle_motion", lambda e: e.label.startswith("V2 white SUV"))
    assert {"sen_v2_001", "wit_w1_03", "wit_w2_04"} <= set(v2_move.observation_ids)
    [rest] = _find(timeline, "post_collision")
    assert set(rest.vehicle_ids) == {"V1", "V2"} and rest.t_start_s == pytest.approx(11.5)


def test_derives_t_rel_from_timestamp(obs_sets):
    for s in obs_sets:
        for o in s.observations:
            o.t_rel_s = None
    tl = build_timeline(obs_sets)
    [col] = _find(tl, "collision")
    assert col.t_start_s == pytest.approx(10.0, abs=0.2)


def test_empty_input():
    assert build_timeline([]).events == []


def test_real_video_shapes_are_resolved(obs_sets):
    """Real module-1 output: free-text approach, other vehicle unnamed, collision names it by label."""
    video, witness, sensor = obs_sets
    by_id = {o.id: o for o in video.observations}
    by_id["vid_002"].attributes["approach"] = "ego lane"
    by_id["vid_003"].vehicle_id = None
    by_id["vid_003"].attributes.update(direction="from left (west)", label="car_2")
    by_id["vid_005"].attributes["vehicles"] = ["V1", "car_2"]
    # sensor bundle also reports the NB green phase starting long before the video
    sensor.observations.append(sensor.observations[-1].model_copy(update={
        "id": "sig_nb_green", "t_rel_s": -20.0,
        "attributes": {"approach": "NB", "signal_state": "green"}}))
    tl = build_timeline(obs_sets)

    [entry] = _find(tl, "vehicle_motion", lambda e: "vid_003" in e.observation_ids)
    assert entry.vehicle_ids == ["V2"]
    [yellow] = _find(tl, "signal", lambda e: "vid_002" in e.observation_ids)
    assert yellow.label == "Northbound signal yellow"
    [green] = _find(tl, "signal", lambda e: e.label == "Northbound signal green")
    assert green.t_start_s == pytest.approx(-20.0) and "vid_001" in green.observation_ids
    [col] = _find(tl, "collision")
    assert set(col.vehicle_ids) == {"V1", "V2"}
    # inputs untouched
    assert by_id["vid_002"].attributes["approach"] == "ego lane"
