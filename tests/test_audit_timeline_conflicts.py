"""Modules 3 and 4 against their requirements: runs scripts/audit_timeline_conflicts.py (hand-built observations)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_timeline_and_contradiction_audit_passes():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "audit_timeline_conflicts.py")],
                       capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0, "\n".join(l for l in r.stdout.splitlines() if l.startswith("FAIL")) or r.stderr[-800:]
