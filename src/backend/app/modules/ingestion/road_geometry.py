"""Road geometry of a fixed-camera view, found from the picture alone: where the road's lane lines, rails and kerbs
meet (the road's vanishing point).

It needs no marking standard, no lane width and no model: the lines of a road converge on one point whatever the
country, and that point fixes how the camera is turned relative to the road. The reconstruction uses it to draw the
vehicles' paths in a road-aligned plan instead of in the camera's own frame (see reconstruction/camera.py).

Nothing here is metric. The result is a direction, never a distance, and it is rejected rather than guessed when the
picture does not support it (too few lines, lines that do not meet, or traffic that does not move along them).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np

MIN_SEGMENT_FRACTION = 0.04    # a line segment must be at least this share of the image diagonal
MIN_SEGMENTS = 6
MAX_SEGMENTS = 400             # the longest N: keeps the search cheap on busy pictures
INLIER_ANGLE_DEG = 2.0         # a segment points at the vanishing point if its direction is within this angle
HORIZONTAL_DEG = 12.0          # segments this close to horizontal are cross-lines (stop lines, the horizon), not the road
RANSAC_ITERATIONS = 1500
# Quality needed before the point is used for anything.
MIN_INLIER_SEGMENTS = 5
MIN_DISTINCT_LINES = 3
MIN_LINE_SPREAD = 0.2          # distinct lines must span this share of the image width (else it is one line seen twice)
MIN_INLIER_LENGTH = 0.5        # total inlier length, in image heights


@dataclass
class VanishingPoint:
    x: float                   # view-local pixels (may lie outside the picture)
    y: float
    segments: int              # line segments found
    inliers: int               # segments that point at the vanishing point
    inlier_length_px: float
    distinct_lines: int        # separate road lines among the inliers
    spread: float              # width of those lines at a reference row, as a share of the image width

    def to_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _segments(gray: np.ndarray) -> np.ndarray:
    """(N, 5) array of [x1, y1, x2, y2, length] for the long, non-horizontal line segments."""
    h, w = gray.shape[:2]
    lines = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(cv2.GaussianBlur(gray, (3, 3), 0))[0]
    if lines is None:
        return np.zeros((0, 5))
    seg = np.asarray(lines, dtype=float).reshape(-1, 4)
    length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    ang = np.degrees(np.arctan2(seg[:, 3] - seg[:, 1], seg[:, 2] - seg[:, 0])) % 180
    keep = (length >= MIN_SEGMENT_FRACTION * np.hypot(w, h)) & (np.minimum(ang, 180 - ang) >= HORIZONTAL_DEG)
    seg, length = seg[keep], length[keep]
    order = np.argsort(-length)[:MAX_SEGMENTS]
    return np.column_stack([seg[order], length[order]])


def estimate_vanishing_point(image: np.ndarray) -> tuple[VanishingPoint | None, str]:
    """The road's vanishing point in `image` (a vehicle-free background), or (None, why not)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    h, w = gray.shape[:2]
    seg = _segments(gray)
    if len(seg) < MIN_SEGMENTS:
        return None, f"too few lane lines or rails in the picture ({len(seg)} long straight segments)"
    length = seg[:, 4]
    mids = np.column_stack([(seg[:, 0] + seg[:, 2]) / 2, (seg[:, 1] + seg[:, 3]) / 2])
    dirs = np.column_stack([seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]])
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    # Each segment's infinite line as a*x + b*y + c = 0 (unit normal).
    a, b = seg[:, 1] - seg[:, 3], seg[:, 2] - seg[:, 0]
    c = seg[:, 0] * seg[:, 3] - seg[:, 2] * seg[:, 1]
    norm = np.hypot(a, b)
    lines = np.column_stack([a / norm, b / norm, c / norm])

    def inliers(vx: float, vy: float) -> np.ndarray:
        to = np.array([vx, vy]) - mids
        dist = np.linalg.norm(to, axis=1) + 1e-9
        cos = np.abs((to / dist[:, None] * dirs).sum(axis=1))
        ang = np.degrees(np.arccos(np.clip(cos, 0, 1)))
        # Road lines lie below the vanishing point and are not right next to it.
        return (ang < INLIER_ANGLE_DEG) & (mids[:, 1] > vy + 0.03 * h) & (dist > 0.15 * h)

    rng = np.random.default_rng(0)
    prob = length / length.sum()
    best: tuple[float, float, float] | None = None
    for _ in range(RANSAC_ITERATIONS):
        i, j = rng.choice(len(seg), 2, replace=False, p=prob)
        p = np.cross(lines[i], lines[j])
        if abs(p[2]) < 1e-9:
            continue
        vx, vy = p[0] / p[2], p[1] / p[2]
        if not (-3 * w < vx < 4 * w and -3 * h < vy < 0.7 * h):
            continue
        score = float(length[inliers(vx, vy)].sum())
        if best is None or score > best[0]:
            best = (score, vx, vy)
    if best is None:
        return None, "the lines in the picture do not meet at any plausible point"
    _, vx, vy = best
    ok = inliers(vx, vy)
    for _ in range(3):  # refine: the point closest, in least squares, to every inlier line
        idx = np.where(ok)[0]
        if len(idx) < 2:
            break
        sol = np.linalg.lstsq(lines[idx, :2] * length[idx, None], -lines[idx, 2] * length[idx], rcond=None)[0]
        vx, vy = float(sol[0]), float(sol[1])
        ok = inliers(vx, vy)
    idx = np.where(ok)[0]
    if len(idx) < MIN_INLIER_SEGMENTS:
        return None, f"only {len(idx)} line segments meet at a common point (need {MIN_INLIER_SEGMENTS})"
    # Distinct lines: where each inlier crosses a reference row well below the vanishing point.
    row = vy + 0.6 * (h - vy)
    xs = np.sort([-(lines[k, 1] * row + lines[k, 2]) / lines[k, 0] for k in idx if abs(lines[k, 0]) > 1e-9])
    distinct = 1 + int((np.diff(xs) > 0.04 * w).sum()) if len(xs) else 0
    spread = float((xs.max() - xs.min()) / w) if len(xs) else 0.0
    total = float(length[idx].sum())
    vp = VanishingPoint(float(vx), float(vy), int(len(seg)), int(len(idx)), total, distinct, spread)
    if distinct < MIN_DISTINCT_LINES or spread < MIN_LINE_SPREAD:
        return None, f"the lines that meet are {distinct} separate line(s) spanning {spread:.0%} of the width: too little to trust"
    if total < MIN_INLIER_LENGTH * h:
        return None, "the lines that meet are too short in total to trust"
    return vp, "ok"


MIN_TRACKS_TO_VETO = 3   # fewer long tracks than this say nothing: one car swerving across lanes must not veto a road


def traffic_agrees(vp: tuple[float, float], tracks: list[list[tuple[float, float, float]]], height: float,
                   tolerance_deg: float = 25.0, min_share: float = 0.5) -> tuple[bool, str]:
    """Cross-check the road lines against the traffic: vehicles that travel far should travel towards or away from
    the vanishing point. The lines are rejected only when enough long tracks (MIN_TRACKS_TO_VETO) mostly disagree;
    with fewer there is nothing to judge by and the lines stand on their own."""
    checked = agree = 0
    for pts in tracks:
        if len(pts) < 3:
            continue
        (_, x0, y0), (_, x1, y1) = pts[0], pts[-1]
        move = np.array([x1 - x0, y1 - y0])
        if np.linalg.norm(move) < 0.1 * height:
            continue
        mid = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        to = np.array(vp) - mid
        if np.linalg.norm(to) < 1e-6:
            continue
        cos = abs(float(move @ to) / (np.linalg.norm(move) * np.linalg.norm(to)))
        checked += 1
        agree += int(np.degrees(np.arccos(min(1.0, cos))) <= tolerance_deg)
    if checked < MIN_TRACKS_TO_VETO:
        return True, f"too few long tracks to check against ({checked})"
    return agree / checked >= min_share, f"{agree} of {checked} long tracks head along the road lines"
