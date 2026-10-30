"""Walking skeleton: API serves fixtures end to end, before any module is built."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_results_served_before_any_run():
    assert len(client.get("/api/observations").json()) == 3
    assert client.get("/api/timeline").json()["events"]
    assert client.get("/api/conflicts").json()["conflicts"]
    assert client.get("/api/reconstruction").json()["vehicles"]
    assert client.get("/api/analysis/latest").json() is None


def test_demo_run_without_evidence_uses_fixtures():
    run = client.post("/api/analysis/run").json()
    assert run["evidence_count"] == 0
    # no uploads: extractors serve their demo fixtures; modules 3-5 run for real once built
    assert {s["status"] for s in run["stages"][:3]} == {"fixture"}
    assert all(s["status"] in ("real", "fixture") for s in run["stages"])
    assert len(run["stages"]) == 6
    assert client.get("/api/analysis/latest").json()["run_id"] == run["run_id"]


def test_upload_list_delete_and_run():
    r = client.post("/api/evidence", files={"file": ("w.txt", b"I saw a red car.", "text/plain")})
    assert r.status_code == 201 and r.json()["kind"] == "witness"
    ev_id = r.json()["id"]
    client.post("/api/evidence", files={"file": ("s.csv", b"a,b\n1,2\n", "text/csv")})
    assert len(client.get("/api/evidence").json()) == 2

    run = client.post("/api/analysis/run").json()
    assert run["evidence_count"] == 2
    # one observation set per source type actually uploaded (fixture-backed for now)
    kinds = sorted(s["source_type"] for s in client.get("/api/observations").json())
    assert kinds == ["sensor", "witness"]

    assert client.delete(f"/api/evidence/{ev_id}").status_code == 204
    assert client.delete(f"/api/evidence/{ev_id}").status_code == 404


def test_upload_rejects_unknown_type():
    r = client.post("/api/evidence", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415
