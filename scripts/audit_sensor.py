"""Audit of module 2b (sensor CSV -> observations) against the requirements and the hand-off contract.

Parse timestamps, normalise speed units, extract vehicle movement, identify missing records, generate
structured observations; inputs untouched. No API key needed. Run: .venv/bin/python scripts/audit_sensor.py
"""
import sys, json, tempfile, hashlib
from pathlib import Path as _P
ROOT=_P(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"/"backend"))
import os; os.chdir(ROOT)
from pathlib import Path
from datetime import datetime, timezone
from app import config
config.apply_incident(json.loads(Path("data/case.json").read_text()))
from app.schemas import Evidence, SourceType, ObservationSet
from app.modules.extraction import extract_sensor
def ev(p):
    p=Path(p); return Evidence(id="ev_"+p.stem[:8], kind=SourceType.sensor, filename=p.name, size_bytes=p.stat().st_size, stored_path=str(p), uploaded_at=datetime.now(timezone.utc))
res=[]
def check(name, ok, detail=""): res.append((name, ok, detail)); print(("PASS" if ok else "FAIL"), name, detail)

before={f: hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in map(str, Path("data/sensor").glob("*.csv"))}
outs={}
for f in sorted(Path("data/sensor").glob("*.csv")):
    o=extract_sensor(ev(f)); outs[f.name]=o
    ObservationSet.model_validate(o.model_dump(mode="json"))  # valid in the common schema
    print("==", f.name, len(o.observations), "obs;", o.warnings)
check("inputs left unmodified", before=={f: hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in before})
# 1 timestamps parsed: every observation has t_rel_s and timestamp
for n,o in outs.items():
    check(f"timestamps parsed ({n})", all(x.t_rel_s is not None and x.timestamp is not None for x in o.observations))
# 2 speed units normalised to km/h with raw kept
v2=outs["v2_obd.csv"]; sp=[x for x in v2.observations if x.attributes.get("speed_mph_raw") is not None]
check("mph -> km/h with raw kept", bool(sp) and all(abs(x.attributes["speed_kmh"]-x.attributes["speed_mph_raw"]*1.609344)<0.15 for x in sp), f"{len(sp)} obs")
# 3 vehicle movement
acts={n:{x.action for x in o.observations} for n,o in outs.items()}
check("movement extracted (moving/braking/heading/stopped)", {"moving","braking","stopped","sudden_heading_change"} <= acts["v1_telematics.csv"], str(acts["v1_telematics.csv"]))
check("full measured path kept", any(x.action=="track" for x in outs["v1_telematics.csv"].observations))
# 4 missing sensor records
gaps=[x for x in outs["v1_telematics.csv"].observations if x.action=="sensor_gap"]
check("missing records -> sensor_gap observation", len(gaps)==1 and gaps[0].attributes["gap_start_s"]==6.0 and gaps[0].attributes["gap_end_s"]==7.5, str([g.attributes for g in gaps]))
check("missing records -> warning", any("no records between" in w for w in outs["v1_telematics.csv"].warnings))
check("no false gaps in complete files", not [x for n in ("v2_obd.csv","signal_controller.csv") for x in outs[n].observations if x.action=="sensor_gap"])
# 5 structured: source refs
check("every obs has file + row locator", all(x.source_ref.locator.startswith(("row:","rows:")) and x.source_ref.file.endswith(".csv") for o in outs.values() for x in o.observations))
check("confidence in [0,1]", all(0<=x.confidence<=1 for o in outs.values() for x in o.observations))
check("unique ids", all(len({x.id for x in o.observations})==len(o.observations) for o in outs.values()))
check("coverage_s set", all(o.coverage_s for o in outs.values()))
# edge cases
def csv(text, name="t.csv"):
    d=Path(tempfile.mkdtemp())/name; d.write_text(text); return ev(d)
e=extract_sensor(csv("timestamp,speed_kmh\n2026-10-12T17:42:02+05:30,50\n2026-10-12T17:42:00+05:30,50\n2026-10-12T17:42:01+05:30,50\n"))
check("unsorted rows are ordered by time", [x.t_rel_s for x in e.observations][0]==0.0, str([x.t_rel_s for x in e.observations]))
try:
    e=extract_sensor(csv("timestamp,speed_kmh\n2026-10-12T17:42:00+05:30,50\nbad-time,50\n2026-10-12T17:42:01+05:30,abc\n2026-10-12T17:42:02+05:30,50\n"))
    check("bad rows skipped with a warning", len(e.warnings)>=2 and bool(e.observations), str(e.warnings))
except Exception as ex: check("bad rows skipped with a warning", False, f"{type(ex).__name__}: {ex}")
for unit,col,val,expect in (("m/s","speed_ms",10,36.0),("knots","speed_knots",10,18.52),("kmh","speed_kph",10,10.0),("'Speed (km/h)'","Speed (km/h)",10,10.0),("'Velocity [mph]'","Velocity [mph]",10,16.09),("'speed_m/s'","speed_m/s",10,36.0),("ft/s","speed_fps",10,10.97)):
    try:
        e=extract_sensor(csv(f"timestamp,{col}\n2026-10-12T17:42:00+05:30,{val}\n2026-10-12T17:42:01+05:30,{val}\n"))
        got=e.observations[0].attributes.get("speed_kmh"); check(f"unit {unit}", got is not None and abs(got-expect)<0.1, f"got {got}")
    except Exception as ex: check(f"unit {unit}", False, f"{type(ex).__name__}: {ex}")
e=extract_sensor(csv("timestamp,speed_kmh\n2026-10-12T17:42:00+05:30,50\n2026-10-12T17:42:01+05:30,50\n2026-10-12T17:42:02+05:30,50\n2026-10-12T17:42:03+05:30,50\n2026-10-12T17:42:04+05:30,50\n2026-10-12T17:42:10+05:30,50\n"))
check("single-file gap detected without vehicle column", any(x.action=="sensor_gap" for x in e.observations), str([x.action for x in e.observations]))
e=extract_sensor(csv("timestamp,speed_kmh\n2026-10-12T17:42:00+05:30,50\n2026-10-12T17:42:01+05:30,\n2026-10-12T17:42:02+05:30,50\n"))
check("blank speed cell reported as missing record", any("unreadable" in w or "missing" in w for w in e.warnings), str(e.warnings))
fails=[r for r in res if not r[1]]; print("\n", len(res)-len(fails), "/", len(res), "passed"); 
