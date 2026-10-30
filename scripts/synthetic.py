"""Synthetic videos with known ground truth, for tests and the evaluation cases.

* render_dashcam: a forward-facing camera driving down a textured road at a given speed profile
* render_cctv: a fixed camera over a road with standard lane markings and vehicles at known speeds

Both use a level pinhole camera, so motion, scale and timing are exactly known.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import cv2
import numpy as np


def _ground_texture(seed: int = 0, w: int = 1024, h: int = 4096) -> np.ndarray:
    """40 m x 80 m of road: blotches ~0.5 m across (asphalt patches, stains) plus dashed lane lines.
    Features must be larger than a pixel's footprint at mid distance, or the render shimmers."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(40, 200, (320, 160), dtype=np.uint8)          # 0.25 m cells
    tex = cv2.resize(cv2.GaussianBlur(coarse, (0, 0), 1.0), (w, h), interpolation=cv2.INTER_CUBIC)
    tex = cv2.normalize(tex, None, 60, 150, cv2.NORM_MINMAX)
    px_m = w / 40.0
    for lane_x in (-5.25, -1.75, 1.75, 5.25):                            # dashed lines, 3 m dash / 6 m gap
        for z in np.arange(0, 80, 9.0):
            x0 = int((lane_x + 20 - 0.08) * px_m)
            cv2.rectangle(tex, (x0, int(z / 80 * h)), (x0 + int(0.16 * px_m), int((z + 3) / 80 * h)), 235, -1)
    return tex


def render_dashcam(path: Path, speed_ms: Callable[[float], float], duration_s: float, fps: int = 15,
                   w: int = 480, h: int = 270, focal: float = 300.0, cam_height: float = 1.4, seed: int = 0) -> Path:
    """Drive along +Z at speed_ms(t). Ground is textured (X in [-20, 20] m, Z repeats every 80 m)."""
    tex = _ground_texture(seed)
    th, tw = tex.shape
    yh, cx = 0.45 * h, w / 2
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    below = yy > yh + 1
    z = np.where(below, focal * cam_height / np.maximum(yy - yh, 1e-3), 1e6).astype(np.float32)
    x = ((xx - cx) * z / focal).astype(np.float32)
    mapx = ((x + 20) / 40 * tw).astype(np.float32)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    dist, n = 0.0, int(duration_s * fps)
    for i in range(n):
        t = i / fps
        mapy = (((z + dist) % 80.0) / 80.0 * th).astype(np.float32)
        ground = cv2.remap(tex, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        frame = np.where(below, ground, np.uint8(205)).astype(np.uint8)
        writer.write(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
        dist += speed_ms(t) / fps
    writer.release()
    return path


def project(x_m: float, z_m: float, focal: float, cam_height: float, cx: float, yh: float) -> tuple[float, float]:
    return cx + focal * x_m / z_m, yh + focal * cam_height / z_m


def render_cctv(path: Path, vehicles: list[dict], duration_s: float, fps: int = 12, w: int = 480, h: int = 300,
                focal: float = 400.0, cam_height: float = 6.0, dash_m: float = 6.0, gap_m: float = 12.0,
                lane_m: float = 3.75) -> Path:
    """A fixed camera looking along a road. vehicles: {"lane_x": m, "z0": m, "speed_ms": +away/-towards,
    "color": (b, g, r)}. Lane lines are dashed to the given standard."""
    cx, yh = w / 2, 0.2 * h
    road = np.full((h, w, 3), 95, np.uint8)
    road[: int(yh) + 1] = (200, 200, 200)
    for lx in (-1.5 * lane_m, -0.5 * lane_m, 0.5 * lane_m, 1.5 * lane_m):
        for z in np.arange(8.0, 200.0, dash_m + gap_m):
            quad = [project(lx - 0.12, z, focal, cam_height, cx, yh), project(lx + 0.12, z, focal, cam_height, cx, yh),
                    project(lx + 0.12, z + dash_m, focal, cam_height, cx, yh),
                    project(lx - 0.12, z + dash_m, focal, cam_height, cx, yh)]
            cv2.fillPoly(road, [np.int32(np.round(quad))], (235, 235, 235), lineType=cv2.LINE_AA)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    for i in range(int(duration_s * fps)):
        t = i / fps
        img = road.copy()
        for v in sorted(vehicles, key=lambda v: -(v["z0"] + v["speed_ms"] * t)):
            z = v["z0"] + v["speed_ms"] * t
            if z < 6 or z > 190:
                continue
            corners = [project(v["lane_x"] + dx, z + dz, focal, cam_height, cx, yh) for dx in (-0.9, 0.9) for dz in (0, 4.5)]
            xs, ys = [c[0] for c in corners], [c[1] for c in corners]
            top = min(ys) - (max(xs) - min(xs)) * 0.6  # body height
            cv2.rectangle(img, (int(min(xs)), int(top)), (int(max(xs)), int(max(ys))), v.get("color", (40, 40, 200)), -1)
        writer.write(img)
    writer.release()
    return path
