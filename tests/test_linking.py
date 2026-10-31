"""Linking the vision model's vehicle boxes across keyframes (fixed cameras): identity from position, size, colour and
a soft label, with the road's direction as a prior. Includes a synthetic benchmark against the previous behaviour
(link on an identical label, nearest first), which fragmented every vehicle the model named inconsistently."""
import math
import re

import numpy as np
import pytest

from app.modules.ingestion.linking import (Sight, assign, colour_of, kind_of, label_distance, link_sightings)

W, H = 1920, 1080


def box(cx, by, size=120.0):
    return (cx - size / 2, by - 0.6 * size, cx + size / 2, by)


def sight(t, label, cx, by, size=120.0, hist=None):
    return Sight(t, label, box(cx, by, size), 0.8, hist)


# ---------------------------------------------------------------------------------------------- labels

def test_label_words_are_understood_loosely():
    assert colour_of("white SUV") == colour_of("silver car") == "light"
    assert colour_of("black sedan") == colour_of("dark car") == "dark"
    assert kind_of("white station wagon") == "car" and kind_of("white minibus") == "van" and kind_of("a person") == "person"
    assert label_distance("white sedan", "white sedan") == 0.0
    assert label_distance("white sedan", "white hatchback") < 0.25      # same colour and kind: only the words differ
    assert label_distance("white sedan", "dark sedan") >= 0.6           # another colour: a different car
    assert label_distance("white car", "white van") >= 0.5              # another kind of vehicle


def test_assignment_is_optimal_not_greedy():
    cost = np.array([[1.0, 2.0], [1.1, 10.0]])    # greedy would give row 0 its best (col 0) and row 1 col 1 = 11.0
    pairs = dict(assign(cost))
    assert pairs == {0: 1, 1: 0}                  # total 3.1
    assert assign(np.zeros((0, 3))) == [] and len(assign(np.ones((3, 2)))) == 2


# ---------------------------------------------------------------------------------------------- linking rules

def test_a_vehicle_the_model_renames_stays_one_track():
    frames = [(float(i), [sight(float(i), name, 400 + 40 * i, 500)]) for i, name in
              enumerate(["white sedan", "white car", "white hatchback", "white sedan", "white car"])]
    (tr,) = link_sightings(frames, W)
    assert len(tr.sights) == 5 and tr.label in {"white sedan", "white car"}


def test_a_changed_colour_word_is_a_penalty_but_changed_pixels_are_a_veto():
    """The model calls one silver SUV both "white" and "dark" on a night clip, so the label alone must not split a track;
    the colours actually seen in the box decide."""
    white = np.zeros(144); white[100] = 1.0
    dark = np.zeros(144); dark[3] = 1.0
    words_only = [(0.0, [sight(0.0, "white SUV", 500, 500, hist=white)]), (0.5, [sight(0.5, "dark SUV", 515, 500, hist=white)])]
    assert len(link_sightings(words_only, W)) == 1             # same pixels, other word: still one vehicle
    pixels = [(0.0, [sight(0.0, "white car", 500, 500, hist=white)]), (0.5, [sight(0.5, "dark car", 515, 500, hist=dark)])]
    assert len(link_sightings(pixels, W)) == 2                 # white pixels became dark pixels: another vehicle


def test_colour_keeps_two_cars_apart_when_they_cross():
    white = np.zeros(144); white[100] = 1.0
    dark = np.zeros(144); dark[3] = 1.0
    f = []
    for i in range(5):
        t = i * 0.5
        f.append((t, [sight(t, "car", 300 + 120 * i, 500, hist=white), sight(t, "car", 900 - 120 * i, 505, hist=dark)]))
    tracks = [tr for tr in link_sightings(f, W) if len(tr.sights) > 1]
    assert len(tracks) == 2
    assert all(len({id(s.hist) for s in tr.sights}) == 1 for tr in tracks)    # no track mixes the two colours


def test_the_road_prior_follows_a_fast_car_that_the_circular_gate_loses():
    vp = (-80.0, -180.0)
    d = np.array(vp) - np.array([1500.0, 900.0])
    d /= np.linalg.norm(d)
    p0, p1 = np.array([1500.0, 900.0]), np.array([1500.0, 900.0]) + d * 650   # moved 650 px along the road in 1 s
    frames = [(0.0, [sight(0.0, "white car", *p0, size=150)]), (1.0, [sight(1.0, "white car", *p1, size=100)])]
    assert len(link_sightings(frames, W)) == 2                         # no road known: a jump of this size is not believable
    assert len(link_sightings(frames, W, vp)) == 1                     # along the road it is


def test_the_road_prior_still_refuses_a_sideways_jump():
    vp = (-80.0, -180.0)
    frames = [(0.0, [sight(0.0, "white car", 1500, 900, size=150)]), (1.0, [sight(1.0, "white car", 1500, 400, size=150)])]
    # (1500, 400) is far off the line from (1500, 900) to the vanishing point
    assert len(link_sightings(frames, W, vp)) == 2


def test_a_vehicle_does_not_reverse_along_the_road():
    vp = (-80.0, -180.0)
    d = np.array(vp) - np.array([1200.0, 800.0])
    d /= np.linalg.norm(d)
    pts = [np.array([1200.0, 800.0]) + d * k for k in (0.0, 150.0, 300.0, 100.0)]   # away, away, then back
    frames = [(float(i), [sight(float(i), "white car", *p, size=140)]) for i, p in enumerate(pts)]
    tracks = link_sightings(frames, W, vp)
    assert max(len(t.sights) for t in tracks) == 3 and len(tracks) == 2


def test_a_missed_keyframe_does_not_break_a_track():
    frames = [(0.0, [sight(0.0, "white van", 800, 400, size=140)]), (0.5, []), (1.0, [sight(1.0, "white van", 860, 410, size=140)])]
    (tr,) = link_sightings(frames, W)
    assert len(tr.sights) == 2


# ---------------------------------------------------------------------------------------------- benchmark

LABELS = {"white car": ["white sedan", "white car", "white hatchback", "white station wagon", "white SUV"],
          "white van": ["white van", "white minibus", "white van"],
          "dark car": ["dark sedan", "black sedan", "dark SUV", "black car", "dark car"],
          "red car": ["red car", "red sedan"], "bus": ["white bus", "white bus", "bus"]}
PEAKS = {"white car": (0, 0, 5), "white van": (0, 0, 5), "dark car": (0, 1, 0), "red car": (0, 3, 3), "bus": (0, 0, 4)}
VP = (-80.0, -180.0)


def _hist(kind, rng):
    h = np.zeros(6 * 4 * 6)
    a, b, c = PEAKS[kind]
    h[(a * 4 + b) * 6 + c] = 0.7
    h += rng.dirichlet(np.ones(len(h)) * 0.5) * 0.3
    return h / h.sum()


def _scene(rng, drift):
    kinds = list(LABELS)
    vehicles = []
    for _ in range(int(rng.integers(3, 8))):
        start = np.array([rng.uniform(300, 1900), rng.uniform(150, 950)])
        d = (np.array(VP) - start) / np.linalg.norm(np.array(VP) - start)
        vehicles.append({"kind": kinds[rng.integers(0, len(kinds))], "start": start, "d": d if rng.random() < 0.6 else -d,
                         "speed": rng.uniform(150, 700), "t0": rng.uniform(0, 3), "dur": rng.uniform(5, 14),
                         "stopped": rng.random() < 0.15, "drift": rng.normal(0, drift)})
    return vehicles


def _position(v, t):
    if t < v["t0"]:
        return None
    tt, sp = min(t - v["t0"], v["dur"]), 0.0 if v["stopped"] else v["speed"]
    perp = np.array([-v["d"][1], v["d"][0]])
    return v["start"] + v["d"] * sp * tt * 0.8 + perp * v["drift"] * max(sp, 200) * tt * 0.8


def _frames(rng, vehicles):
    frames, t = [], 0.0
    while t < 18:
        out = []
        for vi, v in enumerate(vehicles):
            p = _position(v, t)
            if p is None or not (0 < p[0] < W and 0 < p[1] < H) or rng.random() < 0.1:
                continue
            size = max(40, 0.14 * W * (max(p[1], 0) / H + 0.08)) * rng.uniform(0.9, 1.1)
            cx, by = p[0] + rng.normal(0, 0.05 * size), p[1] + rng.normal(0, 0.05 * size)
            label = LABELS[v["kind"]][rng.integers(0, len(LABELS[v["kind"]]))]
            out.append((Sight(t, label, box(cx, by, size), 0.8, _hist(v["kind"], rng)), vi))
        frames.append((t, out))
        t += rng.choice([0.27, 0.5, 0.53, 1.07, 0.8, 1.3])
    return frames


def _old_link(frames):
    """The previous behaviour: identical label words, nearest box within 18% of the frame, first come first served."""
    key = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    tracks = []
    for t, sights in frames:
        for s, vi in sights:
            cx, cy = (s.box[0] + s.box[2]) / 2 / W * 1000, s.box[3] / H * 1000
            near = [(math.hypot(cx - tr["x"], cy - tr["y"]), tr) for tr in tracks
                    if tr["key"] == key(s.label) and t - tr["t"] <= 3.0 and math.hypot(cx - tr["x"], cy - tr["y"]) <= 180]
            tr = min(near, key=lambda n: n[0])[1] if near else None
            if tr is None:
                tr = {"key": key(s.label), "who": []}
                tracks.append(tr)
            tr.update(x=cx, y=cy, t=t)
            tr["who"].append(vi)
    return [tr["who"] for tr in tracks if len(tr["who"]) >= 2]


def _score(tracks, totals):
    purity = sum(np.bincount(w).max() for w in tracks) / max(sum(len(w) for w in tracks), 1)
    best = {}
    for w in tracks:
        v = int(np.bincount(w).argmax())
        best[v] = max(best.get(v, 0), len(w))
    return purity, float(np.mean([best.get(v, 0) / n for v, n in totals.items()]))


def _benchmark(drift, vp, seeds=25):
    old, new = [], []
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        frames = _frames(rng, _scene(rng, drift))
        totals = {}
        for _, sights in frames:
            for _, vi in sights:
                totals[vi] = totals.get(vi, 0) + 1
        totals = {v: n for v, n in totals.items() if n >= 2}
        if not totals:
            continue
        ids = {id(s): vi for _, sights in frames for s, vi in sights}
        linked = [[ids[id(s)] for s in tr.sights]
                  for tr in link_sightings([(t, [s for s, _ in sights]) for t, sights in frames], W, vp) if len(tr.sights) >= 2]
        old.append(_score(_old_link(frames), totals))
        new.append(_score(linked, totals))
    return np.mean(old, axis=0), np.mean(new, axis=0)


def test_benchmark_clean_traffic_with_the_road_known():
    (op, oc), (np_, nc) = _benchmark(0.0, VP)
    assert np_ >= 0.97 and nc >= 0.93                  # one track per vehicle, almost no mixing
    assert nc > oc + 0.4 and np_ > op                  # the old behaviour kept a third of each vehicle's sightings


def test_benchmark_without_a_road_direction_is_still_better_than_before():
    (op, oc), (np_, nc) = _benchmark(0.0, None)
    assert nc > oc + 0.15 and np_ >= op - 0.03          # far more of each vehicle in one track, purity within noise


def test_benchmark_with_sideways_drift_and_a_wrong_road_direction():
    (op, oc), (np_, nc) = _benchmark(0.15, (VP[0] + 250, VP[1] + 120))      # lane changes, and a vanishing point 280 px off
    assert np_ >= 0.94 and nc >= 0.85 and nc > oc + 0.35
    # a vanishing point that is far wrong must not make things worse than not knowing it
    (_, _), (wp, wc) = _benchmark(0.0, (VP[0] + 900, VP[1] + 400))
    (_, _), (zp, zc) = _benchmark(0.0, None)
    assert wp >= zp - 0.06 and wc >= zc - 0.1


# ---------------------------------------------------------------------------------------------- stops from tracks

from app.modules.ingestion.linking import LinkedTrack, stationary_run  # noqa: E402


def _track(points, size=120.0):
    return LinkedTrack([sight(t, "white car", x, y, size) for t, x, y in points])


def test_a_vehicle_that_stays_at_one_place_across_keyframes_is_stopped():
    tr = _track([(0.0, 300, 500), (1.0, 500, 520), (2.0, 700, 540), (3.0, 702, 541), (4.0, 698, 542), (5.5, 701, 540)])
    assert stationary_run(tr, W) == (2, 5)


def test_a_moving_vehicle_has_no_stop():
    assert stationary_run(_track([(float(i), 300 + 150 * i, 500 + 10 * i) for i in range(6)]), W) is None


def test_a_vehicle_creeping_along_is_not_called_stopped():
    assert stationary_run(_track([(float(i), 700 + 45 * i, 540) for i in range(6)]), W) is None   # 45 px/s against a 120 px vehicle


def test_two_keyframes_or_a_short_span_are_not_enough():
    assert stationary_run(_track([(0.0, 300, 500), (1.0, 700, 540), (2.0, 701, 540)]), W) is None
    assert stationary_run(_track([(0.0, 300, 500), (1.0, 700, 540), (1.3, 701, 541), (1.6, 700, 540), (1.9, 702, 541)]), W) is None


def _analysed(rows):
    """rows: per keyframe, [(label, cx, bottom_y)] in 0-1000 units -> (evidence, info, analysed)."""
    import json
    from datetime import datetime, timezone
    from app.modules.ingestion.video import Frame, VideoInfo
    from app.modules.ingestion.vision import parse_frame_analysis
    from app.schemas import Evidence

    img = np.zeros((90, 160, 3), np.uint8)
    frames = [Frame(index=round(t * 30), t_s=t, image=img) for t, _ in rows]
    analysed = [(f, parse_frame_analysis(json.dumps({"vehicles": [
        {"label": label, "box": [cx - 40, by - 70, cx + 40, by], "action": "moving", "direction": "away from camera, left lane", "confidence": 0.9}
        for label, cx, by in sights]}))) for f, (_, sights) in zip(frames, rows)]
    ev = Evidence(id="ev_v", kind="video", filename="v.mp4", size_bytes=1, stored_path="/dev/null", uploaded_at=datetime.now(timezone.utc))
    return ev, VideoInfo(fps=30, frame_count=900, width=W, height=H), analysed


def test_the_pipeline_reports_a_stop_with_the_keyframes_that_show_it():
    from app.modules.ingestion import fixed_box_observations
    rows = [(0.0, [("white car", 100, 500)]), (1.0, [("white car", 170, 510)]), (2.0, [("white sedan", 240, 520)]),
            (3.0, [("white car", 300, 530)]), (4.2, [("white hatchback", 302, 531)]), (5.5, [("white car", 301, 530)])]
    ev, info, analysed = _analysed(rows)
    tracks, stops = fixed_box_observations(ev, info, analysed, [], None)
    assert len(tracks) == 1 and len(stops) == 1
    (stop,) = stops
    assert stop.action == "stopped" and stop.vehicle_id == "U-B1-001" and stop.attributes["seen_in_keyframes"] == 3
    assert stop.attributes["first_seen_s"] == 3.0 and stop.attributes["last_seen_s"] == 5.5
    assert stop.attributes["box"] and stop.attributes["camera"] == "fixed" and "same place" in stop.attributes["verified_by"]
    assert stop.time_uncertainty_s >= 0.5 and stop.confidence <= 0.7


def test_a_vehicle_parked_throughout_is_not_an_incident_event():
    from app.modules.ingestion import fixed_box_observations
    rows = [(float(i), [("white car", 520 + (i % 2), 540)]) for i in range(6)]
    ev, info, analysed = _analysed(rows)
    tracks, stops = fixed_box_observations(ev, info, analysed, [], None)
    assert len(tracks) == 1 and stops == []


def test_a_stop_the_pixel_check_already_confirmed_is_not_repeated():
    from app.modules.ingestion import fixed_box_observations
    from app.schemas import Observation, SourceRef
    rows = [(0.0, [("white car", 100, 500)]), (1.0, [("white car", 170, 510)]), (2.0, [("white car", 240, 520)]),
            (3.0, [("white car", 300, 530)]), (4.2, [("white car", 302, 531)]), (5.5, [("white car", 301, 530)])]
    ev, info, analysed = _analysed(rows)
    known = Observation(id="k", source_type="video", source_ref=SourceRef(evidence_id="ev_v", file="v.mp4", locator="frame:90"), t_rel_s=3.0,
                        action="stopped", confidence=0.8, attributes={"box": [260, 460, 340, 530], "camera": "fixed", "first_seen_s": 3.0, "last_seen_s": 5.5})
    assert fixed_box_observations(ev, info, analysed, [], None, stops_known=[known])[1] == []
    assert len(fixed_box_observations(ev, info, analysed, [], None, stops_known=[])[1]) == 1


def test_a_pedestrian_is_not_a_vehicle_track():
    from app.modules.ingestion import fixed_box_observations
    rows = [(float(i), [("person", 300 + 20 * i, 600)]) for i in range(5)]
    ev, info, analysed = _analysed(rows)
    assert fixed_box_observations(ev, info, analysed, [], None) == ([], [])
