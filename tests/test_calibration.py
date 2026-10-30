"""Camera calibration: lane-dash auto calibration on a synthetic road with a known camera, manual
homography, speeds, and the calibration API."""
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.ingestion import calibration as cal

F, CAM_H, CX, YH, W, H = 400.0, 6.0, 240.0, 60.0, 480, 300   # level pinhole camera: K = F * CAM_H = 2400


@pytest.fixture(autouse=True)
def motorway_markings(monkeypatch):
    """The synthetic road is painted to the de_motorway standard (6 m dash, 12 m gap, 3.75 m lane)."""
    from app import config
    monkeypatch.setattr(config, "LANE_MARKINGS", {"preset": "de_motorway", **config.LANE_PRESETS["de_motorway"]})


def test_no_declared_standard_means_no_automatic_calibration(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "LANE_MARKINGS", None)
    c = cal.auto_calibrate(synthetic_road(), view=1, box=(0, 0, W, H))
    assert not c.ok and "no lane-marking standard" in c.reason


def test_a_wrong_standard_scales_speeds_so_it_must_be_declared(monkeypatch):
    """Same road read as US markings (3.05 m + 9.14 m) gives a 1.5x smaller scale: why it is never guessed."""
    from app import config
    monkeypatch.setattr(config, "LANE_MARKINGS", {"preset": "us_freeway", **config.LANE_PRESETS["us_freeway"]})
    c = cal.auto_calibrate(synthetic_road(), view=1, box=(0, 0, W, H))
    assert c.ok is False or abs(c.k / (F * CAM_H) - (3.048 + 9.144) / 18) < 0.1


def project(x_m, z_m):
    return CX + F * x_m / z_m, YH + F * CAM_H / z_m


def synthetic_road() -> np.ndarray:
    """Grey road with four dashed lane lines (6 m dash, 12 m gap, 3.75 m lanes) seen by the camera above."""
    img = np.full((H, W, 3), 95, np.uint8)
    for lane_x in (-5.625, -1.875, 1.875, 5.625):
        for z in np.arange(14.0, 160.0, 18.0):
            quad = [project(lane_x - 0.12, z), project(lane_x + 0.12, z),
                    project(lane_x + 0.12, z + 6), project(lane_x - 0.12, z + 6)]
            cv2.fillPoly(img, [np.int32(np.round(quad))], (235, 235, 235), lineType=cv2.LINE_AA)
    return img


def test_auto_calibration_recovers_the_camera():
    c = cal.auto_calibrate(synthetic_road(), view=1, box=(0, 0, W, H))
    assert c.ok, c.reason
    assert abs(c.horizon_y - YH) < 4
    assert abs(c.k - F * CAM_H) / (F * CAM_H) < 0.1


def test_speed_of_a_vehicle_at_108_kmh():
    c = cal.auto_calibrate(synthetic_road(), view=1, box=(0, 0, W, H))
    pts = [[t, *project(-2.8, 40 - 30 * t)] for t in np.arange(0, 1.01, 0.1)]  # 30 m/s towards the camera
    _, per_point, mean = cal.track_speeds(c, pts, view_bottom=H)
    assert abs(mean - 108) / 108 < 0.12, mean
    assert per_point


def test_flat_image_is_not_calibrated():
    c = cal.auto_calibrate(np.full((H, W, 3), 95, np.uint8), view=2, box=(0, 0, W, H))
    assert not c.ok and "need" in c.reason and c.ground(100, 200) is None


def test_manual_homography():
    ground = [[0, 0], [3.75, 0], [3.75, 30], [0, 30], [1.875, 15]]
    image = [list(project(x - 1.875, z + 15)) for x, z in ground]
    c = cal.manual_calibration(3, image, ground)
    assert c.ok and c.reprojection_m < 0.05
    gx, gy = c.ground(*project(0.0, 25.0))  # (lateral 0 = x 1.875 in the ground frame, along 10)
    assert abs(gx - 1.875) < 0.05 and abs(gy - 10.0) < 0.05
    for bad in ([[0, 0], [1, 1], [2, 2], [3, 3]],):  # collinear
        try:
            cal.manual_calibration(3, bad, ground[:4])
            raise AssertionError("collinear points accepted")
        except ValueError:
            pass


def test_calibration_api(tmp_path):
    client = TestClient(app)
    path = tmp_path / "clip.avi"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (W, H))
    for _ in range(12):
        w.write(synthetic_road())
    w.release()
    ev = client.post("/api/evidence", files={"file": ("clip.avi", path.read_bytes(), "video/x-msvideo")}).json()
    base = f"/api/evidence/{ev['id']}/calibration"
    assert client.get(base).json() == {"manual": {}, "auto": {}}
    body = {"image_points": [list(project(x, z)) for x, z in [(-3.75, 20), (3.75, 20), (3.75, 50), (-3.75, 50)]],
            "ground_points": [[-3.75, 20], [3.75, 20], [3.75, 50], [-3.75, 50]], "note": "lane corners from map"}
    r = client.put(f"{base}/1", json=body)
    assert r.status_code == 200 and r.json()["method"] == "manual" and r.json()["note"] == "lane corners from map"
    assert client.put(f"{base}/1", json={**body, "image_points": body["image_points"][:3]}).status_code == 422
    assert "1" in client.get(base).json()["manual"]
    assert client.delete(f"{base}/1").status_code == 204
    assert client.delete(f"{base}/1").status_code == 404
    events = [e["event"] for e in client.get("/api/audit").json()]
    assert "calibration_set" in events and "calibration_removed" in events


def test_calibrated_camera_tracks_give_a_top_down_reconstruction(tmp_path, monkeypatch):
    """A synthetic road with de_motorway markings and a car at 100 km/h: the reconstruction is in metres."""
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    from app import config
    from app.modules import ingestion
    from app.modules.reconstruction import build_event_analysis, build_reconstruction
    from app.modules.timeline import build_timeline
    from app.schemas import ConflictReport, Evidence, SourceType

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from synthetic import render_cctv

    monkeypatch.setattr(config, "FEATHERLESS_API_KEY", "")
    monkeypatch.setattr(ingestion, "CAMERA_MODE", "fixed")
    path = render_cctv(tmp_path / "c.avi", [{"lane_x": -1.875, "z0": 6 - 100 / 3.6 * 2.5, "speed_ms": 100 / 3.6}],
                       duration_s=6.0)
    ev = Evidence(id="ev_c", kind=SourceType.video, filename="c.avi", size_bytes=1, stored_path=str(path),
                  uploaded_at=datetime.now(timezone.utc))
    obs = ingestion.analyze_video(ev)
    timeline = build_timeline([obs])
    empty = ConflictReport(incident_id="x", generated_at=datetime.now(timezone.utc), conflicts=[])
    recon = build_reconstruction([obs], timeline, empty)
    assert recon.scene.type == "metric" and recon.scene.frame_note.startswith("camera view 1 road frame")
    assert len(recon.vehicles) == 1
    track = recon.vehicles[0].track
    assert track[-1].y_m > track[0].y_m + 10  # drives away from the camera, in metres (near-field part)
    assert all(abs(p.x_m - (-1.875)) < 0.4 for p in track)  # its lane centre, across the road
    speeds = [p.speed_kmh for p in track if p.speed_kmh]
    assert speeds and abs(sorted(speeds)[len(speeds) // 2] - 100) < 20
    assert build_event_analysis([obs], timeline).scene.type == "camera_image"
