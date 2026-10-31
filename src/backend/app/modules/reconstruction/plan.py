"""Road-aligned plan view from a vanishing point: undo the camera's pitch and yaw so that the road runs straight up
the plan and lanes are parallel, instead of drawing paths in the camera's own (rotated, foreshortened) frame.

Pure geometry, no image access. Inputs: the road's vanishing point in the view (found by
ingestion/road_geometry.py) and the view size. Assumed, and said so wherever the plan is shown: a camera with no
roll, square pixels, the optical centre at the middle of the view and a typical field of view. Those assumptions fix
the *shape* of the road plane; its *size* stays unknown (the plan is in schematic units, not metres).
"""
from __future__ import annotations

import math
from typing import Callable

FOCAL_PER_WIDTH = 0.87          # focal length as a share of the view width (about a 60 degree field of view)
CAMERA_HEIGHT_UNITS = 8.0       # schematic units; fixes the unit of the plan, not a measurement
MIN_ROWS_BELOW_HORIZON = 0.03   # points nearer the horizon than this (share of view height) are clamped: depth explodes


def road_plan(vp: tuple[float, float], width: float, height: float,
              focal_per_width: float = FOCAL_PER_WIDTH) -> Callable[[float, float], tuple[float, float]]:
    """Return ground(u, v) -> (across, along): schematic position on the road plane of a view-local pixel (u, v).
    `along` grows away from the camera in the direction of the road; `across` grows to the right of that direction."""
    f = focal_per_width * width
    cx, cy = width / 2, height / 2
    vx, vy = vp
    d = (( vx - cx) / f, (vy - cy) / f, 1.0)            # the road direction in camera coordinates
    dn = math.sqrt(sum(c * c for c in d))
    e3 = tuple(c / dn for c in d)                      # along the road
    nn = math.hypot(f, cy - vy)
    e2 = (0.0, f / nn, (cy - vy) / nn)                 # ground-plane normal (zero roll: the horizon is level), pointing down
    e1 = (e2[1] * e3[2] - e2[2] * e3[1], e2[2] * e3[0] - e2[0] * e3[2], e2[0] * e3[1] - e2[1] * e3[0])  # across the road

    def ground(u: float, v: float) -> tuple[float, float]:
        # n . r is proportional to the row's distance below the horizon; clamp so far field stays finite.
        rows = max(v - vy, MIN_ROWS_BELOW_HORIZON * height)
        r = ((u - cx) / f, (vy + rows - cy) / f, 1.0)
        scale = CAMERA_HEIGHT_UNITS / (e2[0] * r[0] + e2[1] * r[1] + e2[2] * r[2])
        return (scale * (e1[0] * r[0] + e1[1] * r[1] + e1[2] * r[2]),
                scale * (e3[0] * r[0] + e3[1] * r[1] + e3[2] * r[2]))

    return ground
