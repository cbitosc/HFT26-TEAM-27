"""Module 1: video validation, frame sampling and (mocked) vision analysis."""
import json
from datetime import datetime, timezone

import cv2
import httpx
import numpy as np
import pytest

from app import config
from app.modules import ingestion
from app.modules.ingestion import analyze_video, load_frames
from app.modules.ingestion.video import VideoError, probe_video, sample_indices
from app.modules.ingestion.vision import VisionClient, VisionError, parse_frame_analysis
from app.schemas import Evidence, ObservationSet, SourceType

FPS = 30
N_FRAMES = 60  # 2 s


def _make_video(path, fps=FPS, n=N_FRAMES):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    assert writer.isOpened()
    for i in range(n):
        img = np.full((48, 64, 3), (i * 4) % 255, dtype=np.uint8)
        writer.write(img)
    writer.release()
    return path


def _evidence(path) -> Evidence:
    return Evidence(
        id="ev_test", kind=SourceType.video, filename=path.name, size_bytes=path.stat().st_size,
        stored_path=str(path), uploaded_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def video(tmp_path):
    return _make_video(tmp_path / "clip.avi")


# Scripted model replies keyed by frame time: green -> yellow, ego brakes, SUV enters, collision.
def _reply(t: float) -> str:
    signal = "green" if t < 1.0 else "amber"
    ego = "braking" if t >= 1.0 else "moving"
    obj = {
        "ego": {"action": ego, "cue": "horizon pitches forward" if ego == "braking" else None, "confidence": 0.7},
        "signals": [{"approach": "ego lane", "state": signal, "confidence": 0.9}],
        "vehicles": [],
        "collision": {"visible": t >= 1.6, "vehicles": ["ego", "white SUV"], "impact": "front-right", "confidence": 0.9},
    }
    if t >= 0.6:
        obj["vehicles"] = [{"label": "White SUV", "description": "white SUV from the left",
                            "action": "entering intersection", "direction": "eastbound", "confidence": 0.8}]
    return "```json\n" + json.dumps(obj) + "\n```"


class FakeClient:
    model = "fake-vision"

    def __init__(self, fail_all=False):
        self.calls = 0
        self.fail_all = fail_all

    def describe_frame(self, jpeg_b64, t_s, mode="dashcam", next_jpeg_b64=None, gap_s=0.0):
        self.calls += 1
        assert jpeg_b64  # an encoded frame was sent
        if self.fail_all:
            raise VisionError("boom")
        return parse_frame_analysis(_reply(t_s))

    def close(self):
        pass


def test_probe_valid_video(video):
    info = probe_video(str(video))
    assert info.fps == pytest.approx(FPS)
    assert info.frame_count == N_FRAMES
    assert (info.width, info.height) == (64, 48)
    assert info.duration_s == pytest.approx(2.0)


def test_probe_rejects_non_video(tmp_path):
    bad = tmp_path / "fake.mp4"
    bad.write_text("not a video")
    with pytest.raises(VideoError):
        probe_video(str(bad))
    with pytest.raises(VideoError):
        probe_video(str(tmp_path / "missing.mp4"))


def test_frame_sampling(video, monkeypatch):
    monkeypatch.setattr(ingestion, "SAMPLE_FPS", 3.0)
    info, frames = load_frames(_evidence(video))
    assert [f.index for f in frames] == [0, 10, 20, 30, 40, 50]
    assert [f.t_s for f in frames] == pytest.approx([0, 1 / 3, 2 / 3, 1, 4 / 3, 5 / 3])
    assert frames[0].image.shape == (48, 64, 3)


def test_sample_indices_capped():
    info = type("Info", (), {"fps": 30.0, "frame_count": 3000})()
    idx = sample_indices(info, 5, max_frames=40)
    assert len(idx) == 40 and idx[0] == 0 and idx == sorted(idx) and idx[-1] < info.frame_count


def test_no_key_validates_then_falls_back(video, monkeypatch):
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    with pytest.raises(NotImplementedError, match="60 frames"):
        analyze_video(_evidence(video))


def test_no_key_invalid_video_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"\x00" * 100)
    with pytest.raises(VideoError):
        analyze_video(_evidence(bad))


def test_analyze_with_mock_model(video, monkeypatch):
    monkeypatch.setattr(ingestion, "SAMPLE_FPS", 5.0)
    monkeypatch.setattr(ingestion, "CAMERA_MODE", "dashcam")
    client = FakeClient()
    out = analyze_video(_evidence(video), client=client)
    out = ObservationSet.model_validate(out.model_dump(mode="json"))  # round-trips as JSON
    assert client.calls == 10  # 2 s at 5 fps
    assert out.source_type == SourceType.video and out.producer == "evidence_ingestion:fake-vision"

    by_action = {}
    for o in out.observations:
        by_action.setdefault(o.action, []).append(o)
        n = int(o.source_ref.locator.split(":")[1])
        assert o.source_ref.locator == f"frame:{n}"
        assert o.t_rel_s == pytest.approx(n / FPS)
        assert 0 <= o.confidence <= 1

    # Repeated per-frame states collapse into changes.
    signals = by_action["signal_state"]
    assert [s.attributes["signal_state"] for s in signals] == ["green", "yellow"]
    assert signals[1].source_ref.locator == "frame:30"
    assert [o.vehicle_id for o in by_action["braking"]] == ["V1"]
    suv = by_action["entering_intersection"]
    assert len(suv) == 1 and suv[0].attributes["direction"] == "eastbound"
    (crash,) = by_action["collision"]
    assert crash.t_rel_s == pytest.approx(1.6)
    assert crash.attributes["vehicles"] == ["V1", "white SUV"]
    assert len({o.id for o in out.observations}) == len(out.observations)


def test_analyze_uses_key_from_config(video, monkeypatch):
    """With a key set, the stub no longer raises: it builds a real client."""
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "test-key")
    monkeypatch.setattr(ingestion, "VisionClient", lambda key, url, model: FakeClient())
    out = analyze_video(_evidence(video))
    assert out.observations


def test_all_frames_failing_raises(video):
    with pytest.raises(RuntimeError, match="all"):
        analyze_video(_evidence(video), client=FakeClient(fail_all=True))


def test_parse_is_lenient():
    a = parse_frame_analysis('Sure! {"signals":[{"state":"AMBER","confidence":1.7}],'
                             '"vehicles":[{"label":"bus","action":"zooming"}],"extra":1} trailing')
    assert a.signals[0].state == "yellow" and a.signals[0].confidence == 1.0
    assert a.vehicles[0].action == "moving"
    with pytest.raises(VisionError):
        parse_frame_analysis("no json here")


def test_vision_client_http(monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": _reply(1.2)}}]})

    client = VisionClient("k", "https://example.test/v1", "some-vlm")
    client._http = httpx.Client(base_url="https://example.test/v1", headers=client._http.headers,
                                transport=httpx.MockTransport(handler))
    a = client.describe_frame("AAAA", 1.2)
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "some-vlm"
    assert seen["body"]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert a.ego.action == "braking" and a.signals[0].state == "yellow"


# ------------------------------------------------------------------ fixed-camera mode

from app.modules.ingestion import detect_camera_mode, frames_to_observations  # noqa: E402
from app.modules.ingestion.video import Frame, VideoInfo  # noqa: E402


def _frames(moving: bool, n: int = 8) -> list[Frame]:
    rng = np.random.default_rng(0)
    scene = cv2.GaussianBlur(rng.integers(0, 255, (240, 640), dtype=np.uint8), (7, 7), 0)
    scene = cv2.cvtColor(cv2.normalize(scene, None, 0, 255, cv2.NORM_MINMAX), cv2.COLOR_GRAY2BGR)
    out = []
    for i in range(n):
        img = scene[:, i * 20:i * 20 + 320].copy() if moving else scene[:, :320].copy()
        cv2.rectangle(img, (10 + 15 * i, 100), (40 + 15 * i, 120), (0, 0, 255), -1)  # one small moving car
        out.append(Frame(index=i * 10, t_s=i / 3, image=img))
    return out


def test_camera_mode_detection(monkeypatch):
    monkeypatch.setattr(ingestion, "CAMERA_MODE", "auto")
    assert detect_camera_mode(_frames(moving=False))[0] == "fixed"
    assert detect_camera_mode(_frames(moving=True))[0] == "dashcam"
    monkeypatch.setattr(ingestion, "CAMERA_MODE", "dashcam")
    mode, why = detect_camera_mode(_frames(moving=False))
    assert mode == "dashcam" and "VIDEO_CAMERA_MODE" in why


def test_fixed_mode_drops_ego_and_flowing_traffic():
    replies = [
        {"ego": {"action": "moving", "confidence": 0.9}, "views": 4,
         "vehicles": [{"label": "white van", "view": 2, "action": "moving", "confidence": 0.8}],
         "signals": [{"approach": "ego lane", "state": "green", "confidence": 0.9}],
         "hazards": [], "collision": {"visible": False}},
        {"vehicles": [{"label": "white van", "view": 2, "action": "braking", "confidence": 0.8}],
         "hazards": [{"type": "stopped in lane", "view": 3, "description": "car stopped", "confidence": 0.7}]},
        {"vehicles": [{"label": "white van", "view": 2, "action": "braking", "confidence": 0.8}],
         "hazards": [{"type": "stopped_in_lane", "view": 3, "confidence": 0.7}],
         "collision": {"visible": True, "vehicles": ["white van", "red car"], "view": 2, "confidence": 0.8}},
    ]
    frames = _frames(moving=False, n=3)
    analysed = [(f, parse_frame_analysis(json.dumps(r))) for f, r in zip(frames, replies)]
    info = VideoInfo(fps=30.0, frame_count=90, width=320, height=240)
    obs = frames_to_observations(_evidence_stub(), info, analysed, "fixed")
    assert [o.action for o in obs] == ["braking", "hazard", "collision"]  # no ego, no ego-lane signal, no "moving"
    assert all(o.vehicle_id is None and o.attributes["camera"] == "fixed" for o in obs)
    assert obs[0].attributes["view"] == 2 and obs[1].attributes == {"hazard": "stopped_in_lane", "view": 3, "camera": "fixed"}
    assert obs[2].attributes["vehicles"] == ["white van", "red car"]


def _evidence_stub() -> Evidence:
    return Evidence(id="ev_cctv", kind=SourceType.video, filename="cctv.mp4", size_bytes=1,
                    stored_path="/dev/null", uploaded_at=datetime.now(timezone.utc))


def test_fixed_mode_keeps_signals_only_when_heads_are_visible():
    frames = _frames(moving=False, n=2)
    sig = {"approach": "northbound", "state": "green", "view": 1, "confidence": 0.9}
    analysed = [(frames[0], parse_frame_analysis(json.dumps({"signals": [sig]}))),
                (frames[1], parse_frame_analysis(json.dumps({"signal_heads_visible": True, "signals": [sig]})))]
    info = VideoInfo(fps=30.0, frame_count=90, width=320, height=240)
    obs = frames_to_observations(_evidence_stub(), info, analysed, "fixed")
    assert [(o.action, o.source_ref.locator) for o in obs] == [("signal_state", f"frame:{frames[1].index}")]


def test_vision_client_sends_both_images(monkeypatch):
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = VisionClient("k", "https://example.test/v1", "m")
    client._http = httpx.Client(base_url="https://example.test/v1", transport=httpx.MockTransport(handler))
    client.describe_frame("AAAA", 1.0, mode="fixed", next_jpeg_b64="BBBB", gap_s=0.5)
    content = sent["body"]["messages"][1]["content"]
    assert [c["type"] for c in content] == ["text", "image_url", "image_url"]
    assert "0.50 s later" in content[0]["text"]


def test_parse_repairs_reply_cut_off_by_max_tokens():
    cut = ('{"views": 4, "vehicles": [{"label": "white van", "view": 2, "action": "braking"}, '
           '{"label": "black car", "view": 3, "descr')
    a = parse_frame_analysis(cut)
    assert a.views == 4 and [v.label for v in a.vehicles] == ["white van"]


def test_fixed_mode_keeps_pixel_verified_stops_and_drops_momentary_claims():
    from app.modules.ingestion import aggregate_fixed

    frames = _frames(moving=False, n=4)
    van = {"label": "white van", "view": 4, "action": "stopped", "box": [500, 760, 600, 950], "confidence": 0.9}
    van_again = {**van, "label": "white truck", "box": [510, 770, 610, 960]}  # same spot, other label
    moving = {"label": "black car", "view": 1, "action": "stopped", "box": [100, 100, 200, 200], "confidence": 0.9}
    no_box = {"label": "red car", "view": 2, "action": "stopped", "confidence": 0.9}
    brake = {"label": "blue car", "view": 2, "action": "braking", "confidence": 0.9}
    replies = [
        {"vehicles": [van, moving, brake, no_box], "hazards": [{"type": "stopped_in_lane", "view": 4},
                                                               {"type": "sudden_braking", "view": 2}]},
        {"vehicles": [van_again]},
        {"collision": {"visible": True, "vehicles": ["a", "b"], "view": 3, "confidence": 0.8}},
        {"vehicles": []},
    ]
    analysed = [(f, parse_frame_analysis(json.dumps(r))) for f, r in zip(frames, replies)]
    # Stand-in for the pixel check: the van's patch is static, the black car's is not.
    verify = lambda checks: [{"static": box[0] >= 500} for _, box in checks]  # noqa: E731
    kept, persistence, dropped = aggregate_fixed(analysed, verify)
    assert persistence == {(4, "white van"): (2, frames[0].t_s, frames[1].t_s)}
    assert dropped == {"stopped": 2, "transient": 2}  # moving + no box; braking + sudden_braking
    info = VideoInfo(fps=30.0, frame_count=90, width=320, height=240)
    obs = frames_to_observations(_evidence_stub(), info, kept, "fixed", persistence)
    assert [o.action for o in obs] == ["stopped", "collision"]  # one van, not two
    assert obs[0].attributes["seen_in_keyframes"] == 2 and obs[0].attributes["view"] == 4


def test_static_region_check(tmp_path):
    from app.modules.ingestion.video import static_regions

    path = tmp_path / "cctv.avi"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (200, 100))
    for i in range(80):
        img = np.full((100, 200, 3), 90, np.uint8)
        cv2.rectangle(img, (10, 30), (50, 70), (240, 240, 240), -1)  # parked box: static, has edges
        cv2.rectangle(img, (10, 30), (50, 70), (20, 20, 20), 2)
        x = 100 + (i * 3) % 90
        cv2.rectangle(img, (x, 30), (x + 20, 50), (0, 0, 255), -1)  # moving car on the right half
        w.write(img)
    w.release()
    info = probe_video(str(path))
    parked, moving = static_regions(str(path), info, [(10, [0, 200, 300, 800]), (10, [500, 200, 1000, 600])])
    assert parked["static"] and not moving["static"]


def test_view_is_taken_from_box_position_in_the_grid():
    from app.modules.ingestion import view_of_box

    assert view_of_box([500, 765, 750, 1000], 4) == 4   # bottom-right quadrant
    assert view_of_box([100, 100, 200, 200], 4) == 1
    assert view_of_box([600, 100, 700, 200], 2) == 2
    assert view_of_box([600, 100, 700, 200], 1) is None and view_of_box(None, 4) is None
