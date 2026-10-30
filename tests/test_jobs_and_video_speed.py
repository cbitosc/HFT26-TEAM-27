"""Background jobs, run lock, keyframe selection, vision cache and the frame preview endpoint."""
import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import pipeline
from app.main import app
from app.modules import ingestion
from app.modules.ingestion import analyze_video
from app.modules.ingestion.video import probe_video, select_keyframes
from app.modules.ingestion.vision import parse_frame_analysis

client = TestClient(app)


def _wait_for_job(timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get("/api/analysis/jobs/current").json()
        if job and job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_background_job_reports_stages_and_result():
    job = client.post("/api/analysis/jobs")
    assert job.status_code == 202 and job.json()["state"] in ("running", "done")
    done = _wait_for_job()
    assert done["state"] == "done" and done["run"]["run_id"]
    assert [s["stage"] for s in done["stages"]][-3:] == ["3. timeline", "4. contradictions", "5. reconstruction"]
    assert all(s["status"] in ("real", "fixture") for s in done["stages"])
    assert client.get("/api/analysis/latest").json()["run_id"] == done["run"]["run_id"]


def test_sync_run_refused_while_another_run_holds_the_lock():
    assert pipeline.RUN_LOCK.acquire(blocking=False)
    try:
        assert client.post("/api/analysis/run").status_code == 409
    finally:
        pipeline.RUN_LOCK.release()


def _video(path, n=120, fps=12, event_at=80):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (160, 120))
    for i in range(n):
        img = np.full((120, 160, 3), 90, np.uint8)
        cv2.rectangle(img, (i % 140, 50), (i % 140 + 20, 70), (0, 0, 255), -1)
        if event_at <= i < event_at + 3:
            img[:] = 255  # a sudden flash: the moment of most change
        w.write(img)
    w.release()
    return path


def test_keyframes_include_baseline_and_the_sudden_change(tmp_path):
    path = str(_video(tmp_path / "v.avi"))
    info = probe_video(path)
    keys = select_keyframes(path, info, max_frames=8, scan_fps=4)
    assert len(keys) == 8 and keys[0] == 0 and keys == sorted(keys)
    assert any(78 <= k <= 84 for k in keys), keys  # the flash at frame 80 is picked
    assert min(b - a for a, b in zip(keys, keys[1:])) >= 3


def test_vision_answers_are_cached_per_frame(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app.schemas import Evidence, SourceType

    path = _video(tmp_path / "v.avi", n=48)
    ev = Evidence(id="ev_c", kind=SourceType.video, filename="v.avi", size_bytes=path.stat().st_size,
                  stored_path=str(path), uploaded_at=datetime.now(timezone.utc))

    class Counting:
        model = "counting"
        calls = 0

        def describe_frame(self, jpeg_b64, t_s, mode="dashcam", next_jpeg_b64=None, gap_s=0.0):
            Counting.calls += 1
            Counting.paired = next_jpeg_b64 is not None and gap_s > 0
            return parse_frame_analysis('{"vehicles": [], "signals": []}')

        def close(self):
            pass

    monkeypatch.setattr(ingestion, "MAX_FRAMES", 6)
    seen = []
    first = analyze_video(ev, client=Counting(), progress=lambda d, t, n: seen.append((d, t)))
    calls = Counting.calls
    assert calls == 6 and seen[-1] == (6, 6)
    analyze_video(ev, client=Counting())
    assert Counting.calls == calls  # second run served entirely from the cache
    assert any("keyframes analysed" in w for w in first.warnings)
    # Static background: fixed camera, even though keyframes are far apart.
    assert any(w.startswith("Camera mode: fixed") for w in first.warnings), first.warnings
    assert Counting.paired  # fixed cameras get a second image to judge motion


def test_frame_preview_endpoint(tmp_path):
    data = (_video(tmp_path / "clip.avi", n=24)).read_bytes()
    r = client.post("/api/evidence", files={"file": ("clip.avi", data, "video/x-msvideo")})
    ev_id = r.json()["id"]
    img = client.get(f"/api/evidence/{ev_id}/frames/5?width=100")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert cv2.imdecode(np.frombuffer(img.content, np.uint8), cv2.IMREAD_COLOR).shape[1] == 100
    assert client.get(f"/api/evidence/{ev_id}/frames/999").status_code == 404
