"""Dashcam ego motion on a synthetic drive with known braking and stop times."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from synthetic import render_dashcam  # noqa: E402

from app.modules.ingestion.video import ego_events, ego_motion, probe_video  # noqa: E402


def speed(t):  # 15 m/s, brake from t=4 s at 6 m/s^2 (stopped at 6.5 s), then stand still
    return 15.0 if t < 4 else max(0.0, 15.0 - 6.0 * (t - 4))


def test_ego_motion_finds_braking_and_stop(tmp_path):
    path = render_dashcam(tmp_path / "dash.avi", speed, duration_s=9.0)
    info = probe_video(str(path))
    samples = ego_motion(str(path), info)
    fwd = {round(s.t, 1): s.forward for s in samples}
    assert fwd[2.0] > 3 * max(0.1, fwd[8.0])  # moving clearly faster than when stopped
    events = ego_events(samples)
    actions = [(e["action"], e["t"]) for e in events]
    braking = [t for a, t in actions if a == "braking"]
    stopped = [t for a, t in actions if a == "stopped"]
    assert actions[0][0] == "moving"
    assert braking and 3.4 <= braking[0] <= 5.2, actions
    assert stopped and 5.6 <= stopped[0] <= 7.4, actions


def test_video_module_without_key_reports_ego_motion(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from app import config
    from app.modules import ingestion
    from app.schemas import Evidence, SourceType

    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    monkeypatch.setattr(ingestion, "CAMERA_MODE", "auto")
    path = render_dashcam(tmp_path / "dash.avi", speed, duration_s=9.0)
    ev = Evidence(id="ev_dash", kind=SourceType.video, filename="dash.avi", size_bytes=path.stat().st_size,
                  stored_path=str(path), uploaded_at=datetime.now(timezone.utc))
    out = ingestion.analyze_video(ev)
    assert any(w.startswith("Camera mode: dashcam") for w in out.warnings), out.warnings
    ego = [o for o in out.observations if o.attributes.get("ego")]
    assert ego and all(o.vehicle_id == "EGO" and o.basis == "derived" for o in ego)  # no declared ego vehicle
    assert any(o.action == "braking" and 3.4 <= o.t_rel_s <= 5.2 for o in ego)
    assert out.coverage_s == (0.0, 9.0)


def test_box_tracks_link_the_same_vehicle_across_keyframes():
    import json

    import numpy as np

    from app.modules.ingestion import box_tracks
    from app.modules.ingestion.video import Frame, VideoInfo
    from app.modules.ingestion.vision import parse_frame_analysis
    from datetime import datetime, timezone
    from app.schemas import Evidence, SourceType

    img = np.zeros((90, 160, 3), np.uint8)
    frames = [Frame(index=i * 15, t_s=i * 1.0, image=img) for i in range(4)]
    replies = [{"vehicles": [{"label": "white SUV", "box": [400 + 40 * i, 500, 480 + 40 * i, 600], "confidence": 0.8},
                             {"label": "red car", "box": [100, 500, 160, 560], "confidence": 0.8}]} for i in range(4)]
    replies[2]["vehicles"].pop(1)  # the red car is missed once: still one track
    analysed = [(f, parse_frame_analysis(json.dumps(r))) for f, r in zip(frames, replies)]
    ev = Evidence(id="ev_d", kind=SourceType.video, filename="d.mp4", size_bytes=1, stored_path="/dev/null",
                  uploaded_at=datetime.now(timezone.utc))
    tracks = box_tracks(ev, VideoInfo(fps=15, frame_count=60, width=1600, height=900), analysed)
    by_label = {t.attributes["label"]: t for t in tracks}
    assert set(by_label) == {"white SUV", "red car"} and all(t.basis == "inferred" for t in tracks)
    assert len(by_label["white SUV"].attributes["points"]) == 4 and len(by_label["red car"].attributes["points"]) == 3
    assert by_label["white SUV"].attributes["points"][-1][1:] == [(520 + 560) / 2000 * 1600, 0.55 * 900]
