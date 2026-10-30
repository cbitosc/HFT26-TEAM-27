"""Fusion infers events that need two evidence files side by side."""
from datetime import datetime, timezone
from pathlib import Path

from app.modules.extraction import extract_sensor
from app.modules.fusion import fuse, infer_collisions
from app.schemas import Evidence, Observation, SourceRef, SourceType

DATA = Path(__file__).resolve().parents[1] / "data" / "sensor"


def _ev(name):
    p = DATA / name
    return Evidence(id=f"ev_{p.stem}", kind=SourceType.sensor, filename=name, size_bytes=p.stat().st_size,
                    stored_path=str(p), uploaded_at=datetime.now(timezone.utc))


def test_collision_inferred_from_two_telemetry_files():
    sets = [extract_sensor(_ev(n)) for n in ("v1_telematics.csv", "v2_obd.csv")]
    fused = fuse(sets)
    (c,) = fused.observations
    assert c.action == "collision" and c.attributes["vehicles"] == ["V1", "V2"]
    assert abs(c.t_rel_s - 10.0) <= 0.5  # heading changes are seen at t=10.5; impact one sample earlier
    assert set(c.attributes["inferred_from"]) <= {o.id for s in sets for o in s.observations}


def test_one_file_or_a_seen_collision_infers_nothing():
    assert fuse([extract_sensor(_ev("v1_telematics.csv"))]) is None

    def obs(i, action, vid=None, t=10.0, x=0.0):
        return Observation(id=i, source_type=SourceType.video, source_ref=SourceRef(evidence_id="e", file="f", locator="x"),
                           t_rel_s=t, vehicle_id=vid, action=action, attributes={"x_m": x, "y_m": 0.0}, confidence=0.9)

    seen = [obs("v", "collision"), obs("a", "sudden_heading_change", "V1"), obs("b", "sudden_heading_change", "V2")]
    assert infer_collisions(seen) == []
    far = [obs("a", "sudden_heading_change", "V1"), obs("b", "sudden_heading_change", "V2", x=50.0)]
    assert infer_collisions(far) == []
