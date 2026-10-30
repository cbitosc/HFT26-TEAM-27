"""Analyse evidence files from the command line and write the incident report. No server needed.

    .venv/bin/python scripts/analyze.py data/witness/*.txt data/sensor/*.csv --case data/case.json
    .venv/bin/python scripts/analyze.py clip.mp4 flow.csv --out reports/case-17 --no-narrative

Every file goes through the same upload checks as the API (hash, duplicate, layout), then the full
pipeline runs. The output folder gets report.md, report.json and each run artifact. A fresh,
isolated storage directory is used for every invocation, so it never touches the server's evidence.
Reads FEATHERLESS_API_KEY etc. from the environment or from .env in the repo root.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path, help="video (.mp4/.mov/.avi/.mkv), witness .txt, sensor .csv")
    ap.add_argument("--out", type=Path, default=None, help="output folder (default: reports/<run id>)")
    ap.add_argument("--no-narrative", action="store_true", help="skip the AI-drafted summary")
    ap.add_argument("--case", type=Path, default=None,
                    help="case settings JSON (incident, vehicles, scene, lane_markings), e.g. data/case.json")
    args = ap.parse_args()

    _load_dotenv()
    storage = Path(tempfile.mkdtemp(prefix="incident-"))
    os.environ["STORAGE_DIR"] = str(storage)  # read by app.config at import
    sys.path.insert(0, str(ROOT / "src" / "backend"))
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    if args.case:
        r = client.put("/api/incident", json=json.loads(args.case.read_text()))
        if r.status_code != 200:
            sys.exit(f"case settings rejected: {r.text}")
        print(f"  case {r.json()['incident_id']}: {len(r.json()['vehicles'])} declared vehicle(s), "
              f"scene {r.json()['scene']['type']}, lane markings {(r.json()['lane_markings'] or {}).get('preset', 'none')}")
    ok = 0
    for f in args.files:
        r = client.post("/api/evidence", files={"file": (f.name, f.read_bytes())})
        if r.status_code == 201:
            ok += 1
            ev = r.json()
            print(f"  + {f.name:<32} {ev['kind']:<8} sha256 {ev['sha256'][:12]}…  {json.dumps(ev['details'])[:70]}")
        else:
            print(f"  ! {f.name:<32} rejected ({r.status_code}): {r.json().get('detail')}")
    if not ok:
        print("No usable evidence.", file=sys.stderr)
        return 2

    t = time.time()
    run = client.post("/api/analysis/run").json()
    print(f"\nAnalysis {run['run_id']} finished in {time.time() - t:.1f} s")
    for s in run["stages"]:
        took = f"{s['duration_s']:.1f} s" if s.get("duration_s") is not None else ""
        print(f"  {s['status']:<8} {s['stage']:<52} {took:>7}  {s['message'][:60]}")

    out = args.out or ROOT / "reports" / run["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    report = client.get(f"/api/report?narrative={'false' if args.no_narrative else 'true'}").json()
    (out / "report.md").write_text(report["markdown"])
    (out / "report.json").write_text(json.dumps(report, indent=2))
    for artifact in ("run", "observations", "timeline", "conflicts", "reconstruction"):
        data = client.get(f"/api/runs/{run['run_id']}/{artifact}").json()
        (out / f"{artifact}.json").write_text(json.dumps(data, indent=2))
    print(f"\n{report['headline']}\nReport: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
