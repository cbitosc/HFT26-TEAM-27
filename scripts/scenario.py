"""Ground-truth parameters for the demo incident.

Every generated data file (sensor CSVs, witness statements) and every
hand-written fixture is derived from these numbers, so the pipeline has a
single, consistent story to reconstruct.

Coordinate frame: metres, origin at the centre of the intersection,
+x = east, +y = north. Left-hand traffic (India).

Story: V1 (red sedan, northbound) enters on a late yellow and is braking.
V2 (white SUV, eastbound) jumps its red ~1 s before the all-red phase ends.
They collide in the north-west quadrant of the box at t = 10.0 s.
"""
from datetime import datetime, timedelta, timezone

INCIDENT_ID = "INC-2026-1012-01"
LOCATION = "Main Rd x 5th Ave junction, Gachibowli, Hyderabad"
IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 12, 17, 42, 0, tzinfo=IST)  # reference time, t = 0 s (local clock)
COLLISION_T = 10.0
COLLISION_XY = (-1.75, 1.75)
T_END = 13.0

# Each vehicle: cruise at v (m/s), start braking at t_brake with decel a (m/s^2).
V1 = {
    "id": "V1",
    "description": "red sedan, TS09 AB 1234",
    "axis": "y",          # travels along +y (northbound)
    "lane": -1.75,        # fixed x
    "v": 13.0,            # 46.8 km/h
    "t_brake": 9.0,
    "a": 3.0,
    "heading_deg": 0.0,
}
V2 = {
    "id": "V2",
    "description": "white SUV, TS07 CD 5678",
    "axis": "x",          # travels along +x (eastbound)
    "lane": 1.75,         # fixed y
    "v": 16.0,            # 57.6 km/h, 35.8 mph
    "t_brake": 9.6,
    "a": 5.0,
    "heading_deg": 90.0,
}

# Signal plan (t in seconds relative to T0).
SIGNAL_PHASES = [
    # (t_start, t_end, approach, state)
    (-20.0, 7.0, "NB", "green"),
    (7.0, 10.0, "NB", "yellow"),
    (10.0, 40.0, "NB", "red"),
    (-20.0, 11.0, "EB", "red"),
    (11.0, 40.0, "EB", "green"),
]

# V1 telematics drops out between these times (inclusive bounds excluded).
V1_SENSOR_GAP = (6.0, 7.5)


def _distance(v: float, t_brake: float, a: float, t: float) -> float:
    if t <= t_brake:
        return v * t
    dt = t - t_brake
    t_stop = v / a
    dt = min(dt, t_stop)
    return v * t_brake + v * dt - 0.5 * a * dt * dt


def _speed(v: float, t_brake: float, a: float, t: float) -> float:
    if t <= t_brake:
        return v
    return max(0.0, v - a * (t - t_brake))


def state_at(vehicle: dict, t: float) -> dict:
    """Position (x, y), speed (m/s) and braking flag of a vehicle at time t.

    Pre-collision motion follows the cruise/brake profile; post-collision both
    vehicles slide north-east and stop within ~1.5 s.
    """
    v, tb, a = vehicle["v"], vehicle["t_brake"], vehicle["a"]
    d_coll = _distance(v, tb, a, COLLISION_T)
    start = (COLLISION_XY[1] if vehicle["axis"] == "y" else COLLISION_XY[0]) - d_coll

    if t <= COLLISION_T:
        d = _distance(v, tb, a, t)
        speed = _speed(v, tb, a, t)
        if vehicle["axis"] == "y":
            x, y = vehicle["lane"], start + d
        else:
            x, y = start + d, vehicle["lane"]
        return {"x": x, "y": y, "speed": speed, "braking": t >= tb, "heading": vehicle["heading_deg"]}

    # Post-impact slide: shared NE drift, decaying to rest over 1.5 s.
    dt = min(t - COLLISION_T, 1.5)
    slide = 4.0 * (dt / 1.5) * (2 - dt / 1.5)  # ease-out to 4 m
    cx, cy = COLLISION_XY
    if vehicle["id"] == "V1":
        x, y, heading = cx + slide * 0.8, cy + slide * 0.5, 35.0
    else:
        x, y, heading = cx + slide * 0.9, cy + slide * 0.2, 75.0
    speed = 0.0 if t - COLLISION_T >= 1.5 else 4.0 * (1 - dt / 1.5)
    return {"x": x, "y": y, "speed": speed, "braking": True, "heading": heading}


def signal_state(approach: str, t: float) -> str:
    for t_start, t_end, appr, state in SIGNAL_PHASES:
        if appr == approach and t_start <= t < t_end:
            return state
    return "unknown"
