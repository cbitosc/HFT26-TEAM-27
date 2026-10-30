"""Fixed-camera trajectories: grid detection, motion tracking and the camera-image reconstruction."""
from datetime import datetime, timezone

import cv2
import numpy as np

from app import config
from app.modules import ingestion
from app.modules.ingestion import analyze_video
from app.modules.ingestion.video import detect_grid, probe_video, track_vehicles
from app.modules.reconstruction import build_event_analysis, build_reconstruction
from app.modules.timeline import build_timeline
from app.schemas import ConflictReport, Evidence, SourceType

W, H, FPS, N = 320, 200, 10, 70


def _cctv(path):
    """2x2 grid of static 'roads' with one car moving down view 1 and one across view 4."""
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    rng = np.random.default_rng(0)
    road = cv2.GaussianBlur(rng.integers(60, 140, (H, W), dtype=np.uint8), (9, 9), 0)
    for i in range(N):
        img = cv2.cvtColor(road, cv2.COLOR_GRAY2BGR)
        img[:, W // 2 - 1:W // 2 + 1] = 0  # view separators
        img[H // 2 - 1:H // 2 + 1, :] = 0
        y = 30 + i                          # view 1: moves down 1 px/frame
        cv2.rectangle(img, (60, y), (76, y + 12), (240, 240, 240), -1)
        x = W // 2 + 10 + 2 * i % 130       # view 4: moves right 2 px/frame
        cv2.rectangle(img, (x, 160), (x + 14, 172), (30, 30, 220), -1)
        w.write(img)
    w.release()
    return path


def _ev(path):
    return Evidence(id="ev_cctv", kind=SourceType.video, filename=path.name, size_bytes=path.stat().st_size,
                    stored_path=str(path), uploaded_at=datetime.now(timezone.utc))


def test_grid_detection():
    img = np.full((100, 200, 3), 120, np.uint8)
    assert detect_grid(img) == [(0, 0, 200, 100)]
    img[:, 99:101] = 0
    img[49:51, :] = 0
    assert detect_grid(img) == [(0, 0, 99, 49), (101, 0, 200, 49), (0, 51, 99, 100), (101, 51, 200, 100)]


def test_tracker_follows_moving_cars(tmp_path):
    path = _cctv(tmp_path / "cctv.avi")
    info = probe_video(str(path))
    tracks = track_vehicles(str(path), info, detect_grid(cv2.VideoCapture(str(path)).read()[1]), warmup_s=1.0)
    by_view = {t.view: t for t in tracks}
    assert set(by_view) >= {1, 4}
    down = by_view[1].points
    assert down[-1][2] - down[0][2] >= 25 and abs(down[-1][1] - down[0][1]) < 6  # straight down (to the view edge)
    across = by_view[4].points
    assert across[-1][1] - across[0][1] > 30  # left to right


def test_tracks_without_key_become_a_camera_reconstruction(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    monkeypatch.setattr(ingestion, "CAMERA_MODE", "fixed")
    path = _cctv(tmp_path / "cctv.avi")
    obs = analyze_video(_ev(path))  # no key: tracks only, not "skipped"
    tracks = [o for o in obs.observations if o.action == "track"]
    assert tracks and all(o.attributes["units"] == "px" and len(o.attributes["points"]) >= 2 for o in tracks)
    assert any("no vision-model findings" in w for w in obs.warnings)

    timeline = build_timeline([obs])
    assert timeline.events == []  # raw tracks are not timeline events
    empty = ConflictReport(incident_id="x", generated_at=datetime.now(timezone.utc), conflicts=[])
    # No view is calibrated: there is no top-down reconstruction, and it says why.
    top_down = build_reconstruction([obs], timeline, empty)
    assert top_down.vehicles == [] and "calibrat" in top_down.note
    # The footage with its tracks is event analysis (pixels).
    recon = build_event_analysis([obs], timeline)
    assert recon.scene.type == "camera_image" and recon.scene.units == "px"
    assert recon.scene.image.evidence_id == "ev_cctv" and recon.scene.image.fps == FPS
    assert len(recon.scene.image.views) == 4
    assert {v.vehicle_id[:2] for v in recon.vehicles} >= {"T1", "T4"}
    first = recon.vehicles[0].track
    assert first == sorted(first, key=lambda p: p.t_s)
