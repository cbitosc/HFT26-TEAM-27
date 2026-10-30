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

    def describe_frame(self, jpeg_b64, t_s):
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
