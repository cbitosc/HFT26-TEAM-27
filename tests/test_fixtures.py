"""Fixtures are the contract in example form: they must validate and cross-reference."""
import pytest

from app import store
from app.schemas import ConflictReport, ObservationSet, Reconstruction, Timeline

OBS = ["video_observations.json", "witness_observations.json", "sensor_observations.json"]


@pytest.mark.parametrize("name", OBS)
def test_observation_fixtures_validate(name):
    s = store.load_fixture(name, ObservationSet)
    assert s.observations
    assert all(o.source_type == s.source_type for o in s.observations)


def _all_obs_ids():
    return {o.id for n in OBS for o in store.load_fixture(n, ObservationSet).observations}


def test_observation_ids_unique():
    ids = [o.id for n in OBS for o in store.load_fixture(n, ObservationSet).observations]
    assert len(ids) == len(set(ids))


def test_timeline_references_known_observations():
    tl = store.load_fixture("timeline.json", Timeline)
    known = _all_obs_ids()
    for e in tl.events:
        assert set(e.observation_ids) <= known, e.id
        assert e.t_start_s <= e.t_end_s


def test_conflicts_reference_known_observations():
    rep = store.load_fixture("conflicts.json", ConflictReport)
    known = _all_obs_ids()
    assert {c.type.value for c in rep.conflicts} == {"numeric_mismatch", "semantic", "timestamp_overlap", "missing_evidence"}
    for c in rep.conflicts:
        assert set(c.observation_ids) <= known, c.id


def test_reconstruction_tracks_cover_time_range():
    r = store.load_fixture("reconstruction.json", Reconstruction)
    for v in r.vehicles:
        ts = [p.t_s for p in v.track]
        assert ts == sorted(ts) and ts[0] == r.t_min_s and ts[-1] == r.t_max_s
    assert r.collision and set(r.collision.supporting_observation_ids) <= _all_obs_ids()
