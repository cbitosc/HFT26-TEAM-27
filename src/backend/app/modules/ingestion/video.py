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
