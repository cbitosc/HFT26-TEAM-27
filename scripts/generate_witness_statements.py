"""Generate the demo witness statements into data/witness/.

The statements are written to contain the kinds of signal the witness
extractor must handle: vague vehicle descriptions, relative ordering
("then", "just before"), approximate times, hedged language, and claims
that contradict the sensor record (speed estimate, "sped up", wall-clock
time). The intended contradictions are listed in KNOWN_DISCREPANCIES so
tests and the contradiction module can be checked against them.

Usage: python scripts/generate_witness_statements.py
"""
from pathlib import Path

import scenario as sc

OUT = Path(__file__).resolve().parents[1] / "data" / "witness"

STATEMENTS = {
    "witness_01_pedestrian.txt": f"""\
WITNESS STATEMENT
Incident: {sc.INCIDENT_ID}
Location: {sc.LOCATION}
Witness: W1 (pedestrian, waiting at the north-west corner)
Recorded: 12 Oct 2026, 18:30 IST, by duty officer

I was standing at the corner waiting to cross 5th Ave. It was around 5:40 in
the evening, still a bit light, I didn't check my phone exactly.
The red car, a sedan, was coming up Main Road from the south. I am fairly sure
its light was green when it was approaching. Then a big white SUV came from my
left along 5th Ave really fast, I would say at least 80 km/h. The SUV did not
slow down at all. The red car tried to stop, I heard the brakes, and then they
hit in the middle of the junction. Both cars slid a few metres towards the
petrol bunk side. Nobody got out for maybe a minute.
""",
    "witness_02_shopkeeper.txt": f"""\
WITNESS STATEMENT
Incident: {sc.INCIDENT_ID}
Location: {sc.LOCATION}
Witness: W2 (shop owner, tea stall on the south-east corner)
Recorded: 12 Oct 2026, 18:45 IST, by duty officer

The crash happened at about quarter to six. I was facing the road.
The light for Main Road had already turned yellow. The red car sped up to beat
the light, I am sure of it, it was going fast. The white vehicle, I think it
was an SUV or maybe a van, was coming from the west side. I did not see what
colour the signal was for the white vehicle. The red car went into the junction
first and then the white one hit it on the side. After that a crowd gathered.
""",
}

# Intended discrepancies against ground truth (scenario.py), for test oracles.
KNOWN_DISCREPANCIES = [
    {"witness": "W1", "claim": "NB light green on approach", "truth": "NB yellow from t=7.0 s; V1 enters at ~t=9.9 s"},
    {"witness": "W1", "claim": "V2 speed >= 80 km/h", "truth": f"V2 max {sc.V2['v'] * 3.6:.1f} km/h"},
    {"witness": "W2", "claim": "V1 sped up to beat the light", "truth": "V1 constant speed then braking from t=9.0 s"},
    {"witness": "W2", "claim": "time about 17:45", "truth": f"collision at {sc.T0:%H:%M}:{int(sc.COLLISION_T):02d} IST"},
]


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in STATEMENTS.items():
        (OUT / name).write_text(text)
        print(f"wrote data/witness/{name}")
