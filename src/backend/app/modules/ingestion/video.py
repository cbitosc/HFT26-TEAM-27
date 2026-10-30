"""OpenCV helpers: validate a video file and sample frames from it."""
from __future__ import annotations

import base64
from dataclasses import dataclass

import cv2
import numpy as np


class VideoError(ValueError):
    """The file is not a readable video."""


@dataclass(frozen=True)
class VideoInfo:
    fps: float
    frame_count: int
    width: int
    height: int

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps


@dataclass(frozen=True)
class Frame:
    index: int
    t_s: float  # index / fps, seconds from the start of the clip
    image: np.ndarray  # BGR
    # A second image a moment later (fixed cameras): lets the model tell moving from stopped.
    next_image: np.ndarray | None = None
    next_gap_s: float = 0.0


def probe_video(path: str) -> VideoInfo:
    """Open the video and check it has a usable fps, frame count and first frame."""
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            raise VideoError(f"cannot open video: {path}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not fps or fps <= 0 or fps > 1000:
            raise VideoError(f"video has no valid fps ({fps}): {path}")
        if frame_count <= 0:
            raise VideoError(f"video has no frames: {path}")
        ok, _ = cap.read()
        if not ok:
            raise VideoError(f"cannot decode first frame: {path}")
        return VideoInfo(fps=float(fps), frame_count=frame_count, width=width, height=height)
    finally:
        cap.release()


def sample_indices(info: VideoInfo, sample_fps: float, max_frames: int) -> list[int]:
    """Frame indices at roughly `sample_fps`, thinned to at most `max_frames`."""
    step = max(1, round(info.fps / sample_fps))
    indices = list(range(0, info.frame_count, step))
    if len(indices) > max_frames:
        stride = len(indices) / max_frames
        indices = [indices[int(i * stride)] for i in range(max_frames)]
    return indices


def extract_frames(path: str, info: VideoInfo, indices: list[int]) -> list[Frame]:
    """Read the requested frames sequentially (seeking is unreliable for many codecs)."""
    wanted = set(indices)
    frames: list[Frame] = []
    cap = cv2.VideoCapture(path)
    try:
        idx = 0
        last = max(indices, default=-1)
        while idx <= last:
            ok = cap.grab()
            if not ok:
                break
            if idx in wanted:
                ok, image = cap.retrieve()
                if ok:
                    frames.append(Frame(index=idx, t_s=idx / info.fps, image=image))
            idx += 1
    finally:
        cap.release()
    return frames


def encode_jpeg_b64(image: np.ndarray, max_width: int = 768, quality: int = 80) -> str:
    """Downscale to `max_width` and return a base64 JPEG for the vision model."""
    h, w = image.shape[:2]
    if w > max_width:
        image = cv2.resize(image, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise VideoError("JPEG encoding failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


# A pixel "changed" if its blurred grey level moved by more than this between samples.
_CHANGE_LEVEL = 12
# Median share of changed pixels above which the camera itself is moving. Fixed CCTV keeps a
# static background (~8% change, only the traffic); a moving dashcam changes most of the frame.
MOVING_CAMERA_SHARE = 0.12


def camera_motion_share(frames: list[Frame]) -> float | None:
    """Median share of pixels that change between consecutive sampled frames (None if < 2 frames)."""
    def small(img: np.ndarray) -> np.ndarray:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        g = cv2.resize(g, (160, max(1, round(160 * g.shape[0] / g.shape[1]))))
        return cv2.GaussianBlur(g, (5, 5), 0).astype(np.int16)

    if len(frames) < 2:
        return None
    shrunk = [small(f.image) for f in frames]
    shares = [float(np.mean(np.abs(a - b) > _CHANGE_LEVEL)) for a, b in zip(shrunk, shrunk[1:])]
    return float(np.median(shares))


def select_keyframes(path: str, info: VideoInfo, max_frames: int, scan_fps: float = 4.0) -> list[int]:
    """Frame indices worth sending to the vision model: an even baseline plus the moments of most change.

    The clip is scanned cheaply at `scan_fps` (small blurred greyscale, no model calls). Half of the
    budget goes to evenly spaced frames so nothing long goes unseen; the other half goes to the
    largest jumps in frame-to-frame change (sudden braking, impacts, vehicles entering), keeping
    event picks about a second apart. Always includes the first frame.
    """
    step = max(1, round(info.fps / scan_fps))
    scan = list(range(0, info.frame_count, step))
    if len(scan) <= max_frames:
        return scan

    activity: dict[int, float] = {}
    prev = None
    cap = cv2.VideoCapture(path)
    try:
        idx = 0
        wanted = set(scan)
        while idx <= scan[-1]:
            if not cap.grab():
                break
            if idx in wanted:
                ok, image = cap.retrieve()
                if ok:
                    g = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    g = cv2.GaussianBlur(cv2.resize(g, (160, max(1, round(160 * g.shape[0] / g.shape[1])))), (5, 5), 0)
                    g = g.astype(np.int16)
                    activity[idx] = 0.0 if prev is None else float(np.mean(np.abs(g - prev)))
                    prev = g
            idx += 1
    finally:
        cap.release()
    scanned = sorted(activity)
    if len(scanned) <= max_frames:
        return scanned

    n_base = max(1, max_frames // 2)
    stride = len(scanned) / n_base
    chosen = {scanned[int(i * stride)] for i in range(n_base)}
    # Change in activity (not activity itself): busy-but-steady traffic shouldn't win every pick.
    # A rise is credited to the later frame, a fall to the earlier one: either way, the peak.
    # The first frame has no predecessor (activity 0), so its pair is skipped.
    jumps = sorted(((abs(activity[b] - activity[a]), b if activity[b] >= activity[a] else a)
                    for a, b in zip(scanned[1:], scanned[2:])), reverse=True)
    # Event picks stay ~1 s apart from each other so one event doesn't take the whole budget; they
    # only need to differ from baseline frames. Leftover budget (short clips) relaxes the spacing.
    min_gap = max(step, round(info.fps))
    events: set[int] = set()
    for gap in (min_gap, step):
        for _, idx in jumps:
            if len(chosen) >= max_frames:
                break
            if idx not in chosen and all(abs(idx - e) >= gap for e in events):
                chosen.add(idx)
                events.add(idx)
    return sorted(chosen)


def with_followers(path: str, info: VideoInfo, frames: list[Frame], gap_s: float) -> list[Frame]:
    """Attach the frame `gap_s` later to each keyframe (one sequential read for all of them)."""
    offset = max(1, round(gap_s * info.fps))
    later = {f.index: min(f.index + offset, info.frame_count - 1) for f in frames}
    got = {f.index: f for f in extract_frames(path, info, sorted(set(later.values())))}
    out = []
    for f in frames:
        nxt = got.get(later[f.index])
        if nxt is None or nxt.index == f.index:
            out.append(f)
        else:
            out.append(Frame(f.index, f.t_s, f.image, nxt.image, round((nxt.index - f.index) / info.fps, 3)))
    return out


# A region counts as static if, 2 s and 4 s later, its blurred grey levels moved by at most this on
# average (calibrated on CCTV: a parked van 3-5, passing traffic 8-13, a misread "stopped" car 20+),
# and it has enough edges to contain an object rather than bare road.
STATIC_MAX_DIFF = 6.0
STATIC_MIN_EDGES = 0.05


def static_regions(path: str, info: VideoInfo, checks: list[tuple[int, list[float]]],
                   later_s: tuple[float, ...] = (2.0, 4.0)) -> list[dict]:
    """For each (frame index, box 0-1000) say whether that patch stays unchanged a few seconds later."""
    def at(idx: int, dt: float) -> int:
        j = idx + round(dt * info.fps)
        return j if j < info.frame_count else max(0, idx - round(dt * info.fps))  # near the end: look back

    needed = sorted({i for idx, _ in checks for i in [idx] + [at(idx, d) for d in later_s]})
    images = {f.index: f.image for f in extract_frames(path, info, needed)}

    def patch(img: np.ndarray, box: list[float]) -> np.ndarray | None:
        h, w = img.shape[:2]
        x1, y1, x2, y2 = int(box[0] / 1000 * w), int(box[1] / 1000 * h), int(box[2] / 1000 * w), int(box[3] / 1000 * h)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None
        g = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(g, (5, 5), 0)

    out = []
    for idx, box in checks:
        base = patch(images[idx], box) if idx in images else None
        later = [patch(images[j], box) for j in (at(idx, d) for d in later_s) if j in images]
        if base is None or not later or any(p is None for p in later):
            out.append({"static": False, "reason": "region could not be read"})
            continue
        diff = max(float(np.mean(np.abs(base.astype(np.int16) - p.astype(np.int16)))) for p in later)
        edges = float(cv2.Canny(base, 50, 150).mean() / 255)
        out.append({"static": diff <= STATIC_MAX_DIFF and edges >= STATIC_MIN_EDGES,
                    "max_diff": round(diff, 1), "edges": round(edges, 3)})
    return out


# ------------------------------------------------------------------ fixed-camera tracking

def detect_grid(image: np.ndarray, dark: int = 40, min_share: float = 0.95) -> list[tuple[int, int, int, int]]:
    """Camera views in a multi-view frame, as (x1, y1, x2, y2), left-to-right, top-to-bottom.

    Views are separated by full-length dark lines; with none found, the whole frame is one view.
    """
    g = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    h, w = g.shape

    def cuts(share: np.ndarray, length: int) -> list[int]:
        idx = [i for i in range(length) if share[i] >= min_share and 0.1 * length < i < 0.9 * length]
        groups: list[list[int]] = []
        for i in idx:
            if groups and i - groups[-1][-1] <= 1:
                groups[-1].append(i)
            else:
                groups.append([i])
        return [(grp[0], grp[-1]) for grp in groups]

    cols = cuts((g < dark).mean(axis=0), w)
    rows = cuts((g < dark).mean(axis=1), h)
    xs = [0] + [v for a, b in cols for v in (a, b + 1)] + [w]
    ys = [0] + [v for a, b in rows for v in (a, b + 1)] + [h]
    return [(xs[i], ys[j], xs[i + 1], ys[j + 1]) for j in range(0, len(ys) - 1, 2) for i in range(0, len(xs) - 1, 2)]


@dataclass
class Track:
    view: int                                  # 1-based camera view
    points: list[tuple[float, float, float]]   # (t_s, x_px, y_px) in full-frame pixels
    first_frame: int
    last_frame: int


def track_vehicles(path: str, info: VideoInfo, views: list[tuple[int, int, int, int]],
                   warmup_s: float = 2.0, backgrounds: dict[int, np.ndarray] | None = None) -> list[Track]:
    """Moving-vehicle tracks per camera view: background subtraction + constant-velocity matching.

    Only for fixed cameras. Stationary vehicles fade into the background model and are not tracked
    (verified stopped vehicles come from the vision model instead). Tracks are split where they
    reverse direction and kept only if they last >= 1.5 s, move >= 30 px and are fairly straight,
    which removes most identity swaps in dense, distant traffic.
    """
    subs = [cv2.createBackgroundSubtractorMOG2(history=150, varThreshold=25, detectShadows=True) for _ in views]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    warmup = round(warmup_s * info.fps)
    tracks: list[dict] = []
    active: list[list[dict]] = [[] for _ in views]
    cap = cv2.VideoCapture(path)
    try:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = i / info.fps
            for v, (x1, y1, x2, y2) in enumerate(views):
                mask = subs[v].apply(frame[y1:y2, x1:x2])
                if i < warmup:
                    continue
                mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)[1]  # 127 = shadow
                mask = cv2.dilate(cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel), kernel, iterations=2)
                n, _, stats, cent = cv2.connectedComponentsWithStats(mask)
                vh = y2 - y1
                # Thresholds were tuned on 479x300 views; scale them so any resolution behaves the same.
                area_k = (x2 - x1) * vh / (479 * 300)
                # Skip the far field (top fifth) where vehicles merge; size floor grows towards the camera.
                dets = [(cent[k][0] + x1, cent[k][1] + y1) for k in range(1, n)
                        if cent[k][1] > 0.2 * vh and stats[k][4] >= area_k * (40 + 400 * (cent[k][1] / vh) ** 2)]
                used: set[int] = set()
                for tr in sorted(active[v], key=lambda tr: -len(tr["pts"])):
                    pt, px, py = tr["pts"][-1]
                    vx, vy = tr["vel"]
                    dt = t - pt
                    ex, ey = px + vx * dt, py + vy * dt
                    gate = (6 + 30 * (py - y1) / vh) * vh / 300 + 0.3 * float(np.hypot(vx, vy)) * dt
                    best, best_d = None, gate
                    for j, (dx, dy) in enumerate(dets):
                        if j not in used and (d := float(np.hypot(dx - ex, dy - ey))) < best_d:
                            best, best_d = j, d
                    if best is not None:
                        dx, dy = dets[best]
                        used.add(best)
                        if dt > 0:
                            tr["vel"] = (0.6 * vx + 0.4 * (dx - px) / dt, 0.6 * vy + 0.4 * (dy - py) / dt)
                        tr["pts"].append((t, dx, dy))
                        tr["last"] = i
                for j, (dx, dy) in enumerate(dets):
                    if j not in used:
                        tr = {"view": v + 1, "pts": [(t, dx, dy)], "vel": (0.0, 0.0), "first": i, "last": i}
                        active[v].append(tr)
                        tracks.append(tr)
                active[v] = [tr for tr in active[v] if t - tr["pts"][-1][0] <= 0.5]
            i += 1
    finally:
        cap.release()
    if backgrounds is not None:  # vehicle-free road per view, for lane-marking calibration
        for v, sub in enumerate(subs):
            if (bg := sub.getBackgroundImage()) is not None:
                backgrounds[v + 1] = bg

    out: list[Track] = []
    for tr in tracks:
        vh = views[tr["view"] - 1][3] - views[tr["view"] - 1][1]
        for seg in _split_on_reversal(tr["pts"]):
            if _plausible(seg, min_move=30.0 * vh / 300):
                frames = (round(seg[0][0] * info.fps), round(seg[-1][0] * info.fps))
                out.append(Track(view=tr["view"], points=_smooth(seg), first_frame=frames[0], last_frame=frames[1]))
    return out


def _split_on_reversal(pts: list[tuple[float, float, float]]) -> list[list[tuple[float, float, float]]]:
    segs = [[pts[0]]]
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        u = np.array([b[1] - a[1], b[2] - a[2]])
        w = np.array([c[1] - b[1], c[2] - b[2]])
        nu, nw = np.linalg.norm(u), np.linalg.norm(w)
        if nu > 2 and nw > 2 and float(np.dot(u, w)) / (nu * nw) < 0.3:
            segs.append([b])
        else:
            segs[-1].append(b)
    if len(pts) > 1:
        segs[-1].append(pts[-1])
    return segs


def _plausible(seg: list[tuple[float, float, float]], min_move: float = 30.0) -> bool:
    if len(seg) < 8 or seg[-1][0] - seg[0][0] < 1.5:
        return False
    net = float(np.hypot(seg[-1][1] - seg[0][1], seg[-1][2] - seg[0][2]))
    path = sum(float(np.hypot(b[1] - a[1], b[2] - a[2])) for a, b in zip(seg, seg[1:]))
    return net >= min_move and path > 0 and net / path >= 0.8


def _smooth(seg: list[tuple[float, float, float]], every_s: float = 0.25) -> list[tuple[float, float, float]]:
    """3-point moving average, then thinned to one point per `every_s` (always keeping the last)."""
    xs = np.convolve([p[1] for p in seg], np.ones(3) / 3, mode="same")
    ys = np.convolve([p[2] for p in seg], np.ones(3) / 3, mode="same")
    xs[0], ys[0], xs[-1], ys[-1] = seg[0][1], seg[0][2], seg[-1][1], seg[-1][2]
    out, last_t = [], None
    for k, (t, _, _) in enumerate(seg):
        if last_t is None or t - last_t >= every_s - 1e-9 or k == len(seg) - 1:
            out.append((round(t, 3), round(float(xs[k]), 1), round(float(ys[k]), 1)))
            last_t = t
    return out
