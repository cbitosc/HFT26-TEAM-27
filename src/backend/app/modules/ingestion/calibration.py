"""Camera calibration for fixed-camera views: image pixels -> metres on the road, so tracks get speeds.

Two methods, both per camera view:

* manual (preferred when present): a homography from >= 4 image points with known ground positions,
  e.g. lane-marking corners measured on a map. Full 2D ground coordinates.
* lane_dashes (automatic): standard lane markings are a ruler painted on the road. For a level
  camera, distance along the road is Z = A + K / (y - y_h) for image row y and horizon row y_h.
  y_h comes from the vanishing point of the dashed lane lines; K from the marking period: near end
  of one dash to near end of the next is dash + gap (18 m). Using the same edge of each dash makes
  blur and anti-aliasing cancel (a dash's own length is inflated by them: K came out 16 % low on a
  synthetic road). Median over pairs, so a missing dash (a 36 m pair) doesn't matter. Every lane line
  is checked on its own: most periods must agree and its dashes must measure roughly the dash length.
  A view is calibrated only if at least two lines pass and agree; otherwise it is reported as
  uncalibrated, never guessed.
  Gives distance along the road (enough for speed); lateral position only via lane width.

The marking standard is a case setting (config.LANE_MARKINGS: a preset such as de_motorway or custom
dash/gap/lane width). Without one, no automatic calibration is attempted: guessing the standard would
silently scale every speed by the wrong factor.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

def _standard() -> tuple[float, float, float] | None:
    from app import config
    lm = config.LANE_MARKINGS
    return (float(lm["dash_m"]), float(lm["gap_m"]), float(lm.get("lane_width_m") or 3.5)) if lm else None
PERIOD_TOLERANCE = 0.2   # a pair counts as one period if within 20 % of the median period
MIN_PERIOD_SHARE = 0.6   # ... and most pairs must
DASH_TOLERANCE = 0.45    # dash length check is loose: edges are blurred
MIN_LINES = 2
NEAR_FIELD = 0.35  # use track points in the nearer 65 % of the road below the horizon (far rows amplify errors)


@dataclass
class ViewCalibration:
    view: int
    method: str                         # "lane_dashes" | "manual"
    ok: bool
    reason: str = ""
    # lane_dashes (full-frame pixel coordinates)
    horizon_y: float | None = None
    vanishing_x: float | None = None
    k: float | None = None
    lane_px_per_row: float | None = None  # lane width in px grows by this much per row below the horizon
    # manual
    homography: list[list[float]] | None = None
    reprojection_m: float | None = None
    quality: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    # ---- mapping

    def ground(self, x: float, y: float) -> tuple[float | None, float] | None:
        """(lateral X, along-road Y) in metres for a full-frame pixel, or None if not mappable."""
        if not self.ok:
            return None
        if self.method == "manual":
            p = np.array(self.homography) @ np.array([x, y, 1.0])
            return (float(p[0] / p[2]), float(p[1] / p[2])) if abs(p[2]) > 1e-9 else None
        dy = y - self.horizon_y
        if dy <= 1:
            return None
        along = self.k / dy
        lateral = None
        if self.lane_px_per_row:
            lateral = (x - self.vanishing_x) / (self.lane_px_per_row * dy) * self.quality.get("assumed", {}).get(
                "lane_width_m", 3.5)
        return lateral, along

    def usable(self, y: float, view_bottom: float) -> bool:
        """Points too close to the horizon are unreliable for the automatic method."""
        if self.method == "manual":
            return True
        return y - self.horizon_y > NEAR_FIELD * (view_bottom - self.horizon_y)


# ------------------------------------------------------------------ automatic: lane dashes

def _paint_blobs(bg: np.ndarray) -> list[dict]:
    g = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    h = g.shape[0]
    local = cv2.medianBlur(g, 21)
    mask = ((g.astype(int) - local) > 25).astype(np.uint8) * 255  # paint: much brighter than the road around it
    mask[: int(0.25 * h)] = 0
    n, lab, st, ce = cv2.connectedComponentsWithStats(mask)
    out = []
    for k in range(1, n):
        _, _, _, hh, area = st[k]
        if area < 6 or area > 800 or hh < 3:
            continue
        ys, xs = np.where(lab == k)
        out.append({"c": ce[k], "top": float(ys.min()), "bot": float(ys.max())})
    return out


def _dist(p, d, o) -> float:
    q = np.asarray(p) - o
    return abs(d[0] * q[1] - d[1] * q[0])


def _lines(blobs: list[dict], rng: np.random.Generator) -> list[list[int]]:
    """Groups of >= 5 collinear blobs (dashed lane lines), found by repeated RANSAC."""
    left, found = list(range(len(blobs))), []
    for _ in range(8):
        best = None
        for _ in range(400):
            if len(left) < 5:
                break
            i, j = rng.choice(left, 2, replace=False)
            a, b = blobs[i]["c"], blobs[j]["c"]
            if abs(a[1] - b[1]) < 20:
                continue
            d = (b - a) / np.linalg.norm(b - a)
            inl = [k for k in left if _dist(blobs[k]["c"], d, a) < 2.5]
            if best is None or len(inl) > len(best):
                best = inl
        if not best or len(best) < 5:
            break
        found.append(best)
        left = [k for k in left if k not in best]
    return found


def _fit(points) -> tuple[np.ndarray, np.ndarray]:
    vx, vy, x0, y0 = cv2.fitLine(np.float32(points), cv2.DIST_L2, 0, 0.01, 0.01).ravel()
    return np.array([vx, vy]), np.array([x0, y0])


def _intersect(fits) -> np.ndarray:
    a = [np.array([-d[1], d[0]]) for d, _ in fits]
    b = [n @ o for n, (_, o) in zip(a, fits)]
    return np.linalg.lstsq(np.array(a), np.array(b), rcond=None)[0]


def auto_calibrate(background: np.ndarray, view: int, box: tuple[int, int, int, int]) -> ViewCalibration:
    """Calibrate one view from its vehicle-free background image (view-local pixels)."""
    standard = _standard()
    if standard is None:
        return ViewCalibration(view, "lane_dashes", False, "no lane-marking standard declared for this case "
                               "(set lane_markings: a preset or dash/gap lengths)")
    DASH_M, GAP_M, LANE_WIDTH_M = standard
    x0, y0 = box[0], box[1]
    h = background.shape[0]
    blobs = _paint_blobs(background)
    groups = _lines(blobs, np.random.default_rng(0))
    if len(groups) < MIN_LINES:
        return ViewCalibration(view, "lane_dashes", False, f"found {len(groups)} dashed lane line(s), need {MIN_LINES}")
    fits = [_fit([blobs[k]["c"] for k in g]) for g in groups]

    # Vanishing point supported by the most lines (rails, kerbs and the far carriageway drop out).
    best = None
    for i in range(len(fits)):
        for j in range(i + 1, len(fits)):
            p = _intersect([fits[i], fits[j]])
            if not -0.5 * h < p[1] < 0.6 * h:
                continue
            support = [k for k, (d, o) in enumerate(fits) if _dist(p, d, o) < 4]
            if best is None or len(support) > len(best[1]):
                best = (p, support)
    if best is None or len(best[1]) < MIN_LINES:
        return ViewCalibration(view, "lane_dashes", False, "lane lines do not meet at a common vanishing point")
    vp = _intersect([fits[k] for k in best[1]])
    yh = float(vp[1])

    period_m = DASH_M + GAP_M
    per_line = []
    for k in best[1]:
        line = [b for b in sorted((blobs[i] for i in groups[k]), key=lambda b: -b["bot"]) if b["top"] - yh > 8]
        ks = [period_m / (1 / (b["bot"] - yh) - 1 / (a["bot"] - yh)) for a, b in zip(line, line[1:])
              if a["bot"] - b["bot"] >= 3]
        if len(ks) < 3:
            continue
        kk = float(np.median(ks))
        share = float(np.mean([abs(x - kk) / kk < PERIOD_TOLERANCE for x in ks]))
        dash = float(np.median([kk * (1 / (b["top"] - yh) - 1 / (b["bot"] - yh)) for b in line]))
        ok = share >= MIN_PERIOD_SHARE and abs(dash - DASH_M) / DASH_M < DASH_TOLERANCE
        per_line.append({"k": kk, "periods": len(ks), "consistent_share": round(share, 2), "dash_m": round(dash, 2),
                         "ok": ok, "fit": fits[k]})
    good = [p for p in per_line if p["ok"]]
    quality = {"lines_found": len(groups), "lines_through_vp": len(best[1]),
               "lines_checked": [{k: v for k, v in p.items() if k != "fit"} for p in per_line],
               "assumed": {"dash_m": DASH_M, "gap_m": GAP_M, "lane_width_m": LANE_WIDTH_M}}
    if len(good) < MIN_LINES:
        return ViewCalibration(view, "lane_dashes", False,
                               f"{len(good)} lane line(s) show a consistent {period_m:g} m marking period, need {MIN_LINES}",
                               horizon_y=yh + y0, vanishing_x=float(vp[0]) + x0, quality=quality)
    ks = [p["k"] for p in good]
    k = float(np.median(ks))
    spread = (max(ks) - min(ks)) / k
    if spread > 0.2:
        return ViewCalibration(view, "lane_dashes", False, f"lane lines disagree on scale by {spread:.0%}",
                               horizon_y=yh + y0, vanishing_x=float(vp[0]) + x0, quality=quality)

    # Lateral scale: the smallest spacing between accepted lines at a reference row is one lane.
    yr = yh + 0.6 * (h - yh)
    xs = sorted(float(o[0] + d[0] * (yr - o[1]) / d[1]) for d, o in (p["fit"] for p in good))
    spacing = [b - a for a, b in zip(xs, xs[1:]) if b - a > 3]
    lane_px_per_row = (min(spacing) / (yr - yh)) if spacing else None
    quality.update(scale_spread=round(spread, 3), lines_used=len(good))
    return ViewCalibration(view, "lane_dashes", True, f"{len(good)} lane lines agree on the {period_m:g} m marking "
                           f"period (dashes measure {', '.join(str(p['dash_m']) for p in good)} m vs {DASH_M:g} m)",
                           horizon_y=yh + y0, vanishing_x=float(vp[0]) + x0, k=k,
                           lane_px_per_row=lane_px_per_row, quality=quality)


# ------------------------------------------------------------------ manual

def manual_calibration(view: int, image_points: list[list[float]], ground_points: list[list[float]]) -> ViewCalibration:
    """Homography from >= 4 point pairs (full-frame px -> metres). Raises ValueError if unusable."""
    if len(image_points) != len(ground_points) or len(image_points) < 4:
        raise ValueError("need at least 4 image points and the same number of ground points")
    src = np.float32(image_points)
    dst = np.float32(ground_points)
    if np.linalg.matrix_rank(np.c_[src, np.ones(len(src))]) < 3:
        raise ValueError("image points are collinear")
    hmat, _ = cv2.findHomography(src, dst, 0)
    if hmat is None:
        raise ValueError("could not compute a homography from these points")
    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), hmat).reshape(-1, 2)
    err = float(np.sqrt(np.mean(np.sum((proj - dst) ** 2, axis=1))))
    return ViewCalibration(view, "manual", True, f"{len(src)} reference points, reprojection error {err:.2f} m",
                           homography=hmat.tolist(), reprojection_m=round(err, 3))


def from_dict(d: dict) -> ViewCalibration:
    return ViewCalibration(**{k: v for k, v in d.items() if k in ViewCalibration.__dataclass_fields__})


# ------------------------------------------------------------------ speeds

def track_speeds(cal: ViewCalibration, points: list[list[float]], view_bottom: float):
    """(ground points [[t, X, Y]], per-point speed km/h or None, mean speed km/h or None) for one track."""
    # Skip points at the view's bottom edge: the vehicle is cut off there, so its road contact is not visible.
    usable = [(t, x, y) for t, x, y in points if cal.usable(y, view_bottom) and y < view_bottom - 3]
    mapped = [(t, cal.ground(x, y)) for t, x, y in usable]
    mapped = [(t, g) for t, g in mapped if g is not None]
    ground = [[round(t, 3), None if g[0] is None else round(g[0], 2), round(g[1], 2)] for t, g in mapped]
    if len(mapped) < 3:
        return ground, {}, None
    ts = np.array([t for t, _ in mapped])
    if cal.method == "manual":
        xy = np.array([g for _, g in mapped])
        dist = np.r_[0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    else:
        dist = np.array([g[1] for _, g in mapped])  # distance along the road
    mean = abs(float(np.polyfit(ts, dist, 1)[0])) * 3.6
    per_point = {}
    for i in range(len(ts)):
        a, b = max(0, i - 2), min(len(ts) - 1, i + 2)  # +-0.5 s window at 4 Hz
        if ts[b] > ts[a]:
            per_point[round(float(ts[i]), 3)] = round(abs(float(dist[b] - dist[a]) / float(ts[b] - ts[a])) * 3.6, 1)
    return ground, per_point, round(mean, 1)


# ------------------------------------------------------------------ storage (per evidence file)

def _file(key: str) -> Path:
    from app import config
    return Path(config.STORAGE_DIR) / "calibration" / f"{key}.json"


def load(key: str) -> dict:
    """{"manual": {view: dict}, "auto": {view: dict}} saved for an evidence file (key = sha256 or id)."""
    path = _file(key)
    return json.loads(path.read_text()) if path.exists() else {"manual": {}, "auto": {}}


def save(key: str, data: dict) -> None:
    path = _file(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
