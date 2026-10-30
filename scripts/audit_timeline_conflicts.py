"""Audit of modules 3 (timeline) and 4 (contradictions) against their requirements.

Timeline: collect observations from all sets, normalise timestamps, sort, correlate observations of the same
event, group, emit valid JSON. Contradictions: numeric rules, timestamp overlap, missing evidence, references
to the original observations, no false alarms on consistent evidence; the LLM pass is skipped without a key.
Hand-built observations, so the checks do not depend on the demo scenario. No API key needed.

    .venv/bin/python scripts/audit_timeline_conflicts.py
"""
import copy
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "backend"))
os.chdir(ROOT)
os.environ["FEATHERLESS_API_KEY"] = ""

from app import config  # noqa: E402
from app.modules.contradictions import detect_conflicts  # noqa: E402
from app.modules.timeline import build_timeline  # noqa: E402
from app.schemas import ConflictReport, Observation, ObservationSet, SourceRef, SourceType, Timeline  # noqa: E402

config.FEATHERLESS_API_KEY = ""
IST = timezone(timedelta(hours=5, minutes=30))
REF = datetime(2026, 10, 12, 17, 45, 0, tzinfo=IST)
config.INCIDENT_REFERENCE_TIME = REF

res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("PASS" if ok else "FAIL"), name, detail)


_n = [0]


def ob(src, action, t=None, unc=0.2, vid=None, ts=None, conf=0.9, desc=None, loc=None, raw=None, **attrs):
    _n[0] += 1
    pre = {"sensor": "sen", "video": "vid", "witness": "wit_w1"}[src]
    return Observation(
        id=f"{pre}_{_n[0]:03d}", source_type=SourceType(src),
        source_ref=SourceRef(evidence_id=f"ev_{src}", file=f"{src}.dat", locator=loc or f"row:{_n[0]}"),
        t_rel_s=t, timestamp=ts, time_uncertainty_s=unc, vehicle_id=vid, vehicle_description=desc,
        action=action, attributes=attrs, confidence=conf, raw_text=raw)


def oset(src, *obs, coverage=None):
    return ObservationSet(source_type=SourceType(src), evidence_id=f"ev_{src}", producer="audit",
                          generated_at=datetime.now(timezone.utc), observations=list(obs), coverage_s=coverage)


def run(*sets):
    tl = build_timeline(list(sets))
    return tl, detect_conflicts(list(sets), tl)


def events_with(tl, oid):
    return [e for e in tl.events if oid in e.observation_ids]


# ============================================================ TIMELINE
sensor_brake = ob("sensor", "braking", t=9.0, vid="V1", speed_kmh=40)
video_brake = ob("video", "braking", t=9.2, unc=0.5, vid="V1")
other_brake = ob("sensor", "braking", t=9.1, vid="V2")
late_brake = ob("sensor", "braking", t=30.0, vid="V1")
coll_s = ob("sensor", "collision", t=12.0, vehicles=["V1", "V2"])
sets = [oset("sensor", sensor_brake, other_brake, late_brake, coll_s), oset("video", video_brake)]
tl, _ = run(*sets)

all_ids = {o.id for s in sets for o in s.observations}
on_tl = {i for e in tl.events for i in e.observation_ids}
check("1 collects observations from all sets (none lost)", all_ids <= on_tl, f"missing {all_ids - on_tl}")

utc_ts = (REF + timedelta(seconds=5)).astimezone(timezone.utc)
only_ts = ob("sensor", "moving", ts=utc_ts, unc=0.1, vid="V3", speed_kmh=30)
naive_ts = ob("sensor", "stopped", ts=(REF + timedelta(seconds=20)).replace(tzinfo=None), unc=0.1, vid="V4")
tl2, _ = run(oset("sensor", only_ts, naive_ts))
e_ts = events_with(tl2, only_ts.id)[0]
e_nv = events_with(tl2, naive_ts.id)[0]
check("2 normalises a UTC timestamp to seconds from the reference", abs(e_ts.t_start_s - 5) < 0.01, f"t={e_ts.t_start_s}")
check("2 reads a naive timestamp in the case time zone", abs(e_nv.t_start_s - 20) < 0.01, f"t={e_nv.t_start_s}")
check("2 event timestamp = reference + t_start_s", abs((e_ts.timestamp - (REF + timedelta(seconds=5))).total_seconds()) < 0.01)

ts_ = [e.t_start_s for e in tl.events]
check("3 events sorted chronologically", ts_ == sorted(ts_), str(ts_))
shuf = copy.deepcopy(sets)
for s in shuf:
    random.Random(7).shuffle(s.observations)
shuf.reverse()
tl3, _ = run(*shuf)
sig = lambda t: [(e.label, e.t_start_s, tuple(sorted(e.observation_ids))) for e in t.events]  # noqa: E731
check("3 same timeline for shuffled input", sig(tl) == sig(tl3))

e = events_with(tl, sensor_brake.id)[0]
check("4 sensor+video braking of one vehicle -> one event",
      video_brake.id in e.observation_ids and set(e.source_types) == {SourceType.sensor, SourceType.video},
      f"{e.label} {e.source_types}")
check("4 another vehicle's braking stays separate", other_brake.id not in e.observation_ids)
check("4 same vehicle braking 20 s later stays separate", late_brake.id not in e.observation_ids)
check("5 collision grouped as its own category",
      any(x.category == "collision" and coll_s.id in x.observation_ids for x in tl.events))

Timeline.model_validate(tl.model_dump(mode="json"))
check("6 timeline JSON validates", True)
check("6 event ids unique", len({e.id for e in tl.events}) == len(tl.events))
check("6 t_end >= t_start and confidence 0..1", all(e.t_end_s >= e.t_start_s and 0 <= e.confidence <= 1 for e in tl.events))

before = copy.deepcopy(sets)
run(*sets)
check("inputs not mutated", [s.model_dump() for s in sets] == [s.model_dump() for s in before])
tl_e, cr_e = run()
check("empty input -> empty timeline and report", tl_e.events == [] and isinstance(cr_e, ConflictReport))
tl_u, _ = run(oset("witness", ob("witness", "braking", vid="V1", raw="I heard brakes", order=1)))
check("witness-only claim with no time is still listed", len(tl_u.events) == 1, str([e.label for e in tl_u.events]))
tl_x, _ = run(oset("sensor", ob("sensor", "moving", t=1.0, vid="V1"), ob("sensor", "moving", t=1.0, vid="V1")))
check("two identical-time observations of one vehicle merge", len(tl_x.events) == 1, str(len(tl_x.events)))


# ====================================================== CONTRADICTIONS
def refs_ok(cr, sets):
    ids = {o.id for s in sets for o in s.observations}
    return all(set(c.observation_ids) <= ids and c.observation_ids for c in cr.conflicts)


samples = [ob("sensor", "moving", t=float(t), vid="V2", speed_kmh=50.0, heading_deg=270) for t in range(0, 12)]
w_fast = ob("witness", "moving", vid="V2", raw="at least 80 km/h", speed_kmh=80, speed_qualifier="at least", order=1)
w_ok = ob("witness", "moving", vid="V2", raw="about 55 km/h", speed_kmh=55, order=2)
coll = ob("sensor", "collision", t=12.0, vehicles=["V1", "V2"])
sets_c = [oset("sensor", *samples, coll, coverage=(0, 12)), oset("witness", w_fast, w_ok)]
tl_c, cr_c = run(*sets_c)
num = [c for c in cr_c.conflicts if c.type.value == "numeric_mismatch"]
check("N1 numeric: 80 km/h claim vs 50 km/h record flagged", any(w_fast.id in c.observation_ids for c in num), f"{len(num)} numeric")
check("N2 numeric: 55 vs 50 km/h within tolerance NOT flagged", not any(w_ok.id in c.observation_ids for c in num))
check("N3 conflict cites original observations that exist", refs_ok(cr_c, sets_c))
check("N4 rule conflicts are labelled basis=rule", all(c.basis == "rule" for c in cr_c.conflicts))

w_up = ob("witness", "moving", vid="V2", raw="no more than 60 km/h", speed_kmh=60, speed_qualifier="no more than", order=1)
_, cr_up = run(oset("sensor", *samples, coll, coverage=(0, 12)), oset("witness", w_up))
check("N5 'no more than 60' vs 50 km/h is consistent",
      not any(w_up.id in c.observation_ids for c in cr_up.conflicts if c.type.value == "numeric_mismatch"))

w_late = ob("witness", "collision", ts=REF + timedelta(seconds=-600), unc=120, raw="about 5:35", clock_time="5:35")
w_close = ob("witness", "collision", ts=REF + timedelta(seconds=10), unc=300, raw="about 5:45", clock_time="5:45")
coll_ts = ob("sensor", "collision", t=12.0, ts=REF + timedelta(seconds=12), unc=0.5, vehicles=["V1", "V2"])
sets_t = [oset("sensor", coll_ts), oset("witness", w_late, w_close)]
_, cr_t = run(*sets_t)
ov = [c for c in cr_t.conflicts if c.type.value == "timestamp_overlap"]
check("T1 clock time 10 min off (unc 2 min) flagged", any(w_late.id in c.observation_ids for c in ov), f"{[c.title for c in ov]}")
check("T2 clock time within its own uncertainty is cited only as consistent",
      all("consistent" in c.description for c in ov if w_close.id in c.observation_ids))
check("T3 overlap conflict cites the sensor observation", any(coll_ts.id in c.observation_ids for c in ov))

gap = ob("sensor", "sensor_gap", t=6.0, vid="V1", gap_start_s=6.0, gap_end_s=7.5, gap_s=1.5)
brk = ob("sensor", "braking", t=7.0, unc=0.5, vid="V1")
v_samples = [ob("sensor", "moving", t=float(t), vid="V1", speed_kmh=40) for t in (0, 2, 4)]
coll_m = ob("sensor", "collision", t=12.0, vehicles=["V1", "V2"])
sets_m = [oset("sensor", gap, brk, *v_samples, coll_m, coverage=(0, 8)),
          oset("video", ob("video", "moving", t=2.0, vid="V2"), coverage=(0, 5))]
_, cr_m = run(*sets_m)
miss = [c for c in cr_m.conflicts if c.type.value == "missing_evidence"]
check("M1 sensor gap overlapping an event reported", any(gap.id in c.observation_ids for c in miss), f"{[c.title for c in miss]}")
check("M2 evidence ending before the collision reported",
      any("cover" in (c.title + c.description).lower() or "ends" in (c.title + c.description).lower() for c in miss),
      f"{len(miss)} missing_evidence")
check("M3 missing-evidence conflicts cite observations", refs_ok(cr_m, sets_m))

c_v1 = [ob("sensor", "moving", t=float(t), vid="V1", speed_kmh=50.0) for t in range(0, 10)]
c_brk_s = ob("sensor", "braking", t=8.0, vid="V1")
c_brk_v = ob("video", "braking", t=8.2, unc=0.4, vid="V1")
c_coll_s = ob("sensor", "collision", t=10.0, vehicles=["V1", "V2"])
c_coll_v = ob("video", "collision", t=10.1, unc=0.3, vehicles=["V1", "V2"])
c_w = ob("witness", "braking", vid="V1", raw="the car braked", order=1)
c_w2 = ob("witness", "moving", vid="V1", raw="going about 50 km/h", speed_kmh=50, order=2)
sets_n = [oset("sensor", *c_v1, c_brk_s, c_coll_s, coverage=(0, 12)), oset("video", c_brk_v, c_coll_v, coverage=(0, 12)),
          oset("witness", c_w, c_w2)]
_, cr_n = run(*sets_n)
bad = [c for c in cr_n.conflicts if c.type.value in ("numeric_mismatch", "timestamp_overlap", "semantic") or c.severity.value == "high"]
check("F1 consistent evidence -> no numeric/overlap/semantic/high conflict", not bad,
      f"{[(c.type.value, c.title) for c in bad]}")

w_nb = ob("witness", "no_braking", vid="V1", raw="the car never braked", order=1)
_, cr_o = run(oset("sensor", *c_v1, c_brk_s, c_coll_s, coverage=(0, 12)), oset("witness", w_nb))
check("O1 'never braked' vs recorded braking flagged", any(w_nb.id in c.observation_ids for c in cr_o.conflicts),
      f"{[c.title for c in cr_o.conflicts]}")

# ---- source against source (found missing in the first audit pass)
g1 = [ob("sensor", "moving", t=float(t), vid="V1", speed_kmh=50.0) for t in range(10)]
g2 = [ob("sensor", "moving", t=float(t) + 0.1, vid="V1", speed_kmh=80.0) for t in range(10)]
for o in g1:
    o.source_ref.evidence_id = "ev_gps"
for o in g2:
    o.source_ref.evidence_id = "ev_obd"
_, cr_ss = run(oset("sensor", *g1, coverage=(0, 10)), oset("sensor", *g2, coverage=(0, 10)))
check("S1 two sensors disagree on one vehicle's speed", any(c.type.value == "numeric_mismatch" and "speed differs" in c.title for c in cr_ss.conflicts))
g3 = [ob("sensor", "moving", t=float(t) + 0.1, vid="V1", speed_kmh=53.0) for t in range(10)]
for o in g3:
    o.source_ref.evidence_id = "ev_obd"
_, cr_s2 = run(oset("sensor", *g1, coverage=(0, 10)), oset("sensor", *g3, coverage=(0, 10)))
check("S2 two sensors within tolerance: no conflict", not [c for c in cr_s2.conflicts if "speed differs" in c.title])
_, cr_ct = run(oset("sensor", ob("sensor", "collision", t=10.0, unc=0.3, vehicles=["V1", "V2"])),
               oset("video", ob("video", "collision", t=16.0, unc=0.5, vehicles=["V1", "V2"])))
check("S3 sensor and video place the collision 6 s apart -> flagged", any(c.type.value == "timestamp_overlap" for c in cr_ct.conflicts))
_, cr_ct2 = run(oset("sensor", ob("sensor", "collision", t=10.0, unc=0.3, vehicles=["V1", "V2"])),
                oset("video", ob("video", "collision", t=10.6, unc=0.5, vehicles=["V1", "V2"])))
check("S4 sensor and video agree within uncertainty: not flagged", not any(c.type.value == "timestamp_overlap" for c in cr_ct2.conflicts))
wa = ob("witness", "signal_state", raw="the light was green", signal_state="green", approach="NB", order=1)
wb = ob("witness", "signal_state", raw="the light was red", signal_state="red", approach="NB", order=1)
wa.id, wb.id = "wit_w1_901", "wit_w2_901"
_, cr_ww = run(oset("witness", wa), oset("witness", wb))
check("S5 two witnesses name different lights, no record -> flagged", any(wa.id in c.observation_ids and wb.id in c.observation_ids for c in cr_ww.conflicts))
wo1 = ob("witness", "entering_intersection", vid="V1", raw="the red car went first", order=1)
wo2 = ob("witness", "entering_intersection", vid="V2", raw="then the white one", order=2)
_, cr_or = run(oset("sensor", ob("sensor", "entering_intersection", t=5.0, vid="V2"), ob("sensor", "entering_intersection", t=8.0, vid="V1"),
                    ob("sensor", "collision", t=9.0, vehicles=["V1", "V2"])), oset("witness", wo1, wo2))
check("S6 witness order of arrival contradicts the record -> flagged", any("orders" in c.title for c in cr_or.conflicts))
_, cr_or2 = run(oset("sensor", ob("sensor", "entering_intersection", t=8.0, vid="V2"), ob("sensor", "entering_intersection", t=5.0, vid="V1"),
                     ob("sensor", "collision", t=9.0, vehicles=["V1", "V2"])), oset("witness", wo1, wo2))
check("S7 witness order matches the record: not flagged", not any("orders" in c.title for c in cr_or2.conflicts))
tl_pc, _ = run(oset("sensor", *[ob("sensor", "moving", t=float(t), vid="V1", speed_kmh=60.0) for t in range(10)],
                    ob("sensor", "collision", t=9.0, vehicles=["V1", "V2"])),
               oset("witness", ob("witness", "stopped", vid="V1", raw="it came to rest", order=1)))
rest = [e for e in tl_pc.events if e.category == "post_collision"]
check("S8 an untimed 'came to rest' claim is not placed before the collision", bool(rest) and all(e.t_start_s >= 9.0 for e in rest), str([(e.label, e.t_start_s) for e in rest]))

_, cr_a = run(*sets_c)
_, cr_b = run(*sets_c)
strip = lambda r: [(c.type, c.severity, c.title, tuple(c.observation_ids)) for c in r.conflicts]  # noqa: E731
check("deterministic", strip(cr_a) == strip(cr_b))
ConflictReport.model_validate(cr_a.model_dump(mode="json"))
check("report JSON validates", True)
check("no key -> no LLM conflicts, rules stand alone", all(c.basis == "rule" for c in cr_a.conflicts))
order = ["high", "medium", "low"]
sev = [c.severity.value for c in cr_a.conflicts]
check("severity ordered high -> low", sev == sorted(sev, key=order.index))

print(f"\n{sum(res)}/{len(res)} checks passed")
sys.exit(0 if all(res) else 1)
