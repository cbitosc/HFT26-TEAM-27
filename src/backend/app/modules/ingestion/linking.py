"""Link the vision model's vehicle boxes across keyframes into one track per vehicle (fixed cameras).

The model answers one keyframe at a time, seconds apart, and does not name vehicles consistently: the same white car
comes back as "white sedan", "white car" and "white hatchback". Linking on the label alone (what a dashcam needs, where
nothing else is stable) therefore breaks one vehicle into several tracks. With the camera fixed, position is the best
clue, so each keyframe's boxes are matched to the tracks so far by *optimal assignment* on a cost made of

* where the track should be by now (its own velocity), relative to how big the vehicle is,
* how the box size changed,
* how the label changed (same words, same colour, same kind of vehicle: soft, never decisive), and
* how its colours changed, measured on the pixels of the keyframe.

A pair is accepted only if it is plausible on its own terms: a vehicle never jumps across the frame, and a white car does
not become a dark one.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

MAX_GAP_S = 3.0               # a vehicle unseen for longer starts a new track when it reappears
BASE_GATE_PER_BOX = 1.1       # allowed jump = this x the vehicle's size (plus what its speed explains)
MIN_GATE_FRACTION = 0.05      # ... and never less than this share of the frame width
MAX_COST = 1.7                # a pair costing more than this is not linked
MAX_VEHICLE_WIDTHS_PER_S = 14  # with a known road direction: how far along it a vehicle may travel (~90 km/h = 14 widths/s)
ACROSS_GATE_WIDTHS = 0.8       # ... and how far it may drift sideways (lane change, perspective), in vehicle sizes
W_SIZE, W_LABEL, W_APPEARANCE = 0.3, 0.5, 0.6

COLOURS = {"white": "light", "silver": "light", "grey": "mid", "gray": "mid", "black": "dark", "dark": "dark",
           "red": "red", "blue": "blue", "green": "green", "yellow": "yellow", "orange": "orange", "brown": "dark",
           "beige": "light", "light": "light", "maroon": "red"}
KINDS = {"car": "car", "sedan": "car", "hatchback": "car", "suv": "car", "wagon": "car", "coupe": "car", "saloon": "car",
         "taxi": "car", "jeep": "car", "van": "van", "minibus": "van", "minivan": "van", "bus": "bus", "truck": "truck",
         "lorry": "truck", "pickup": "truck", "motorcycle": "bike", "motorbike": "bike", "scooter": "bike",
         "bicycle": "bike", "person": "person", "pedestrian": "person"}


def _words(label: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", (label or "").lower()).split()


def colour_of(label: str) -> str | None:
    return next((COLOURS[w] for w in _words(label) if w in COLOURS), None)


def kind_of(label: str) -> str | None:
    return next((KINDS[w] for w in _words(label) if w in KINDS), None)


def label_distance(a: str, b: str) -> float:
    """0 for the same words, up to 1 for a clearly different vehicle (other colour or kind). Soft by design."""
    if _words(a) == _words(b):
        return 0.0
    ca, cb, ka, kb = colour_of(a), colour_of(b), kind_of(a), kind_of(b)
    d = 0.0
    if ca and cb and ca != cb:
        d += 0.6
    if ka and kb and ka != kb:
        d += 0.5
    if d == 0.0 and (ca or cb or ka or kb):
        d = 0.15 if (ca == cb and ka == kb) else 0.1   # same colour and kind in other words, or one side says less
    return min(1.0, d if d else 0.2)


@dataclass
class Sight:
    t: float
    label: str
    box: tuple[float, float, float, float]   # pixels: x1, y1, x2, y2
    confidence: float = 0.5
    hist: np.ndarray | None = None           # normalised colour histogram of the box pixels, if the frame was available
    index: int | None = None                 # frame number the sighting came from
    direction: str | None = None             # the model's words for where it was heading

    @property
    def contact(self) -> tuple[float, float]:
        """Where the vehicle meets the road: bottom centre of its box."""
        return (self.box[0] + self.box[2]) / 2, self.box[3]

    @property
    def size(self) -> float:
        return max(self.box[2] - self.box[0], self.box[3] - self.box[1], 1.0)


@dataclass
class LinkedTrack:
    sights: list[Sight] = field(default_factory=list)

    @property
    def last(self) -> Sight:
        return self.sights[-1]

    def velocity(self) -> tuple[float, float]:
        """Pixels per second from the last two sightings (zero for a vehicle seen once or not seen for a while)."""
        if len(self.sights) < 2:
            return 0.0, 0.0
        a, b = self.sights[-2], self.sights[-1]
        dt = b.t - a.t
        if dt <= 0 or dt > MAX_GAP_S:
            return 0.0, 0.0
        return (b.contact[0] - a.contact[0]) / dt, (b.contact[1] - a.contact[1]) / dt

    @property
    def label(self) -> str:
        """The label seen most often (ties: the most confident sighting)."""
        votes: dict[str, float] = {}
        for s in self.sights:
            votes[s.label] = votes.get(s.label, 0.0) + 1 + 0.01 * s.confidence
        return max(votes, key=votes.get)


def histogram(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
    """Hue/saturation/brightness histogram of the middle of a box: what the vehicle looks like, not what is behind it."""
    import cv2
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    x1, x2 = int(max(0, x1 + 0.15 * bw)), int(min(w, x2 - 0.15 * bw))
    y1, y2 = int(max(0, y1 + 0.15 * bh)), int(min(h, y2 - 0.15 * bh))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    crop = cv2.resize(image[y1:y2, x1:x2], (24, 24), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [6, 4, 6], [0, 180, 0, 256, 0, 256]).ravel()
    total = hist.sum()
    return hist / total if total > 0 else None


def _appearance_distance(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None:
        return 0.3                                   # unknown: neither for nor against
    bc = float(np.sum(np.sqrt(a * b)))
    return float(math.sqrt(max(0.0, 1.0 - bc)))     # Hellinger distance, 0 identical .. 1 disjoint


def assign(cost: np.ndarray) -> list[tuple[int, int]]:
    """Minimum-cost one-to-one assignment (Hungarian algorithm; the matrices here are a handful of vehicles square)."""
    n, m = cost.shape
    if n == 0 or m == 0:
        return []
    size = max(n, m)
    big = float(cost.max()) + 1.0
    padded = np.full((size, size), big)
    padded[:n, :m] = cost
    u, v = np.zeros(size + 1), np.zeros(size + 1)
    p, way = np.zeros(size + 1, dtype=int), np.zeros(size + 1, dtype=int)
    for i in range(1, size + 1):
        p[0], j0 = i, 0
        minv, used = np.full(size + 1, np.inf), np.zeros(size + 1, dtype=bool)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], np.inf, 0
            for j in range(1, size + 1):
                if not used[j]:
                    cur = padded[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(size + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return [(int(p[j]) - 1, j - 1) for j in range(1, size + 1) if p[j] - 1 < n and j - 1 < m]


def _position_cost(tr: "LinkedTrack", s: Sight, dt: float, frame_width: float,
                   vp: tuple[float, float] | None) -> float | None:
    """How implausible it is that sighting `s` is where `tr` went, or None if it cannot be. With the road's vanishing
    point known, a vehicle is expected to move along the road (towards or away from that point): the gate is long along
    it and tight across it, so a car that crossed half the frame between two keyframes can still be followed. Without
    it, the gate is a circle that grows with the speed already seen."""
    last, (vx, vy) = tr.last, tr.velocity()
    size = max(last.size, s.size)
    if vp is not None:
        ux, uy = vp[0] - last.contact[0], vp[1] - last.contact[1]
        norm = math.hypot(ux, uy)
        if norm > 1e-6:
            ux, uy = ux / norm, uy / norm
            rx, ry = s.contact[0] - last.contact[0], s.contact[1] - last.contact[1]
            along, across = rx * ux + ry * uy, abs(rx * uy - ry * ux)
            gate_across = ACROSS_GATE_WIDTHS * size + 0.12 * abs(along)
            gate_along = max(BASE_GATE_PER_BOX * size, MAX_VEHICLE_WIDTHS_PER_S * size * dt)
            if across > gate_across or abs(along) > gate_along:
                return None
            if (vx, vy) != (0.0, 0.0):
                expected = vx * ux + vy * uy                     # px/s along the road so far
                if expected * along < 0 and abs(along) > 0.6 * size and abs(expected) * dt > 0.6 * size:
                    return None                                  # vehicles do not reverse along the road
                return across / gate_across + 0.5 * abs(along - expected * dt) / (0.8 * abs(expected) * dt + BASE_GATE_PER_BOX * size)
            return across / gate_across + 0.25 * min(1.0, abs(along) / gate_along)
    px, py = last.contact[0] + vx * dt, last.contact[1] + vy * dt
    gate = max(BASE_GATE_PER_BOX * size, MIN_GATE_FRACTION * frame_width) + 0.35 * math.hypot(vx, vy) * dt
    dist = math.hypot(s.contact[0] - px, s.contact[1] - py)
    return None if dist > gate else dist / gate


def link_sightings(frames: list[tuple[float, list[Sight]]], frame_width: float,
                   vp: tuple[float, float] | None = None) -> list[LinkedTrack]:
    """Tracks over keyframes. `frames` is [(t, sightings)] in any order; `vp` is the road's vanishing point in the same
    pixel frame, if known (see road_geometry.py)."""
    tracks: list[LinkedTrack] = []
    for t, sights in sorted(frames, key=lambda f: f[0]):
        live = [tr for tr in tracks if t - tr.last.t <= MAX_GAP_S and t > tr.last.t]
        cost = np.full((len(live), len(sights)), 1e6)
        for i, tr in enumerate(live):
            last = tr.last
            dt = t - last.t
            for j, s in enumerate(sights):
                where = _position_cost(tr, s, dt, frame_width, vp)
                if where is None:
                    continue
                grow = abs(math.log((s.size * s.size) / (last.size * last.size)))   # area change, log scale
                if grow > math.log(8 if vp is None else 14):
                    continue
                label = label_distance(tr.label, s.label)
                look = _appearance_distance(last.hist, s.hist)
                c = where + W_SIZE * grow + W_LABEL * label + W_APPEARANCE * look
                if label >= 1.0 or look > 0.85:      # clearly another vehicle, whatever the position says
                    continue
                if c <= MAX_COST:
                    cost[i, j] = c
        taken: set[int] = set()
        for i, j in assign(cost):
            if cost[i, j] < 1e5:
                live[i].sights.append(sights[j])
                taken.add(j)
        for j, s in enumerate(sights):
            if j not in taken:
                tracks.append(LinkedTrack([s]))
    return tracks
