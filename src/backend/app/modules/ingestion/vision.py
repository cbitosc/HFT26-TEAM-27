"""Featherless vision model (OpenAI-compatible chat API): one video frame -> FrameAnalysis.

Two prompts: a forward-facing dashcam (there is an ego vehicle) and fixed cameras
(CCTV / traffic cameras, possibly a grid of several views; there is no ego vehicle).
"""
from __future__ import annotations

import json
import re
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

VEHICLE_ACTIONS = (
    "moving", "braking", "stopped", "turning", "accelerating",
    "entering_intersection", "crossing_intersection", "changing_lanes",
)
HAZARD_TYPES = ("stopped_in_lane", "sudden_braking", "swerve", "wrong_way", "debris", "pedestrian_on_road", "other")
CameraMode = Literal["dashcam", "fixed"]
SIGNAL_STATES = ("red", "yellow", "green", "unknown")

SYSTEM_PROMPT = """You analyse single frames from a forward-facing dashcam for a traffic-incident investigation.
Report only what is visible. Answer with ONE JSON object and nothing else, using this shape:
{
  "ego": {"action": "moving|braking|stopped|turning|accelerating|entering_intersection|crossing_intersection",
          "cue": "what in the frame shows this", "confidence": 0.0-1.0} or null,
  "vehicles": [{"label": "short stable name, colour + type, e.g. 'white SUV'",
                "box": [x1, y1, x2, y2] (the vehicle in the image, 0-1000),
                "description": "one short sentence",
                "action": "moving|braking|stopped|turning|accelerating|entering_intersection|crossing_intersection",
                "direction": "where it comes from / goes, e.g. 'from left (west), eastbound'",
                "confidence": 0.0-1.0}],
  "signals": [{"approach": "which approach the light controls, e.g. 'ego lane' or 'cross street'",
               "state": "red|yellow|green|unknown", "confidence": 0.0-1.0}],
  "collision": {"visible": true|false, "vehicles": ["ego", "<label>"], "impact": "short description",
                "confidence": 0.0-1.0}
}
Use empty lists when nothing is visible. Lower the confidence when the view is blurred, occluded or ambiguous."""

FIXED_PROMPT = """You analyse single frames from FIXED roadside traffic cameras (CCTV) for a traffic-incident investigation.
The frame may be a grid of several camera views; number them 1, 2, ... left-to-right, top-to-bottom.
There is NO ego vehicle: the cameras are mounted on poles or gantries. You may get TWO images of the same
cameras a fraction of a second apart: compare them. A vehicle is "stopped" only if it is in the same place
in both images; a vehicle that moved is not stopped, even if it looks still in one image. With a single
image, do not call anything stopped unless it is clearly parked, crashed or blocking.
Ordinary flowing traffic is not evidence; report only vehicles doing something notable (braking hard,
stopped in a lane, changing lanes abruptly, turning, wrong way) and anything abnormal.
Traffic lights: motorways and highways usually have none. Overhead signs, gantries and lane markings are
NOT signals. Set "signal_heads_visible" to true only if you can see an actual red/amber/green light head.
Answer with ONE JSON object and nothing else, using this shape:
{
  "views": <number of camera views in the frame>,
  "signal_heads_visible": true|false,
  "vehicles": [{"label": "short stable name, colour + type, e.g. 'white van'",
                "view": <view number>,
                "box": [x1, y1, x2, y2] — REQUIRED for "stopped": the vehicle's box, 0-1000 over the WHOLE image,
                "description": "one short sentence",
                "action": "braking|stopped|turning|accelerating|changing_lanes|moving",
                "direction": "relative to the camera, e.g. 'towards camera, right lane'",
                "confidence": 0.0-1.0}],
  "hazards": [{"type": "stopped_in_lane|sudden_braking|swerve|wrong_way|debris|pedestrian_on_road|other",
               "view": <view number>, "description": "one short sentence", "confidence": 0.0-1.0}],
  "signals": [{"approach": "which approach the light controls", "state": "red|yellow|green|unknown",
               "view": <view number>, "confidence": 0.0-1.0}],
  "collision": {"visible": true|false, "vehicles": ["<label>", "<label>"], "view": <view number>,
                "impact": "short description", "confidence": 0.0-1.0}
}
Use empty lists when nothing notable is visible and "visible": false when there is no collision.
Be brief: at most 5 vehicles and 3 hazards, the most important first; descriptions under 12 words;
never list ordinary moving traffic. Lower the confidence when the view is small, blurred, occluded
or ambiguous."""
PROMPTS = {"dashcam": SYSTEM_PROMPT, "fixed": FIXED_PROMPT}


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @field_validator("confidence", mode="before", check_fields=False)
    @classmethod
    def _clamp(cls, v):
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.5


def _norm_action(v) -> str:
    s = re.sub(r"[\s-]+", "_", str(v or "").strip().lower())
    return s if s in VEHICLE_ACTIONS else "moving"


class EgoState(_Lenient):
    action: str = "moving"
    cue: str | None = None
    confidence: float = 0.5

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, v):
        return _norm_action(v)


def _view(v) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _box(v) -> list[float] | None:
    """[x1, y1, x2, y2] in 0-1000 image coordinates, or None if unusable."""
    try:
        x1, y1, x2, y2 = (min(1000.0, max(0.0, float(c))) for c in v)
    except (TypeError, ValueError):
        return None
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    return [x1, y1, x2, y2] if x2 - x1 >= 5 and y2 - y1 >= 5 else None


class VehicleSighting(_Lenient):
    label: str
    view: int | None = None
    box: list[float] | None = None
    description: str | None = None
    action: str = "moving"
    direction: str | None = None
    confidence: float = 0.5

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, v):
        return _norm_action(v)

    _v = field_validator("view", mode="before")(lambda cls, v: _view(v))
    _b = field_validator("box", mode="before")(lambda cls, v: _box(v) if v is not None else None)


class HazardSighting(_Lenient):
    type: str = "other"
    view: int | None = None
    description: str | None = None
    confidence: float = 0.5

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v):
        s = re.sub(r"[\s-]+", "_", str(v or "").strip().lower())
        return s if s in HAZARD_TYPES else "other"

    _v = field_validator("view", mode="before")(lambda cls, v: _view(v))


class SignalSighting(_Lenient):
    approach: str | None = None
    view: int | None = None
    state: Literal["red", "yellow", "green", "unknown"] = "unknown"
    confidence: float = 0.5

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, v):
        s = str(v or "").strip().lower()
        s = {"amber": "yellow", "orange": "yellow"}.get(s, s)
        return s if s in SIGNAL_STATES else "unknown"

    _v = field_validator("view", mode="before")(lambda cls, v: _view(v))


class CollisionSighting(_Lenient):
    visible: bool = False
    vehicles: list[str] = Field(default_factory=list)
    impact: str | None = None
    view: int | None = None
    confidence: float = 0.5

    _v = field_validator("view", mode="before")(lambda cls, v: _view(v))


class FrameAnalysis(_Lenient):
    ego: EgoState | None = None
    views: int | None = None
    signal_heads_visible: bool | None = None
    vehicles: list[VehicleSighting] = Field(default_factory=list)
    hazards: list[HazardSighting] = Field(default_factory=list)
    signals: list[SignalSighting] = Field(default_factory=list)
    collision: CollisionSighting | None = None

    _v = field_validator("views", mode="before")(lambda cls, v: _view(v))


class VisionError(RuntimeError):
    """The model call failed or returned something unusable."""


def _close_truncated(text: str) -> object | None:
    """Best effort for a reply cut off by max_tokens: keep everything up to the last complete
    value and close the open brackets. Returns the parsed object, or None."""
    cuts = [i for i, ch in enumerate(text) if ch in "}]"][::-1][:60]
    for cut in cuts:
        prefix = text[:cut + 1]
        stack, in_str, esc = [], False, False
        for ch in prefix:
            if in_str:
                esc = not esc and ch == "\\"
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        if in_str:
            continue
        try:
            return json.loads(prefix + "".join(reversed(stack)))
        except json.JSONDecodeError:
            continue
    return None


def parse_frame_analysis(text: str) -> FrameAnalysis:
    """Pull the first JSON object out of the reply (tolerates code fences, prose and a reply
    truncated by max_tokens) and validate it."""
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start = cleaned.find("{")
    if start < 0:
        raise VisionError(f"no JSON object in model reply: {text[:120]!r}")
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as e:
        obj = _close_truncated(cleaned[start:])
        if obj is None:
            raise VisionError(f"invalid JSON from model: {e}") from e
    if not isinstance(obj, dict):
        raise VisionError("model reply is not a JSON object")
    try:
        return FrameAnalysis.model_validate(obj)
    except ValidationError as e:
        raise VisionError(f"model JSON failed validation: {e.error_count()} errors") from e


class VisionClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout_s: float = 60.0):
        self.model = model
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )

    def close(self) -> None:
        self._http.close()

    def describe_frame(self, jpeg_b64: str, t_s: float, retries: int = 3, mode: CameraMode = "dashcam",
                       next_jpeg_b64: str | None = None, gap_s: float = 0.0) -> FrameAnalysis:
        kind = "Dashcam frame" if mode == "dashcam" else "Fixed-camera frame"
        images = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{jpeg_b64}"}}]
        text = f"{kind} at t={t_s:.2f} s. Return the JSON object."
        if next_jpeg_b64:
            images.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{next_jpeg_b64}"}})
            text = (f"{kind}s: the first image is t={t_s:.2f} s, the second is {gap_s:.2f} s later. "
                    "Report the situation at the first image. Return the JSON object.")
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 450,  # the JSON fits easily; fewer output tokens is most of the latency
            "messages": [
                {"role": "system", "content": PROMPTS[mode]},
                {"role": "user", "content": [
                    {"type": "text", "text": text},
                    *images,
                ]},
            ],
        }
        last: Exception | None = None
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(min(8.0, 1.5 * 2 ** (attempt - 1)))  # back off: 1.5 s, 3 s, 6 s
            try:
                r = self._http.post("/chat/completions", json=payload)
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
                return parse_frame_analysis(text)
            except httpx.HTTPStatusError as e:
                last = e
                if e.response.status_code in (401, 403, 404):  # key, gated or unknown model: retrying won't help
                    break
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, VisionError) as e:
                last = e
        raise VisionError(f"frame t={t_s:.2f}s: {type(last).__name__}: {last}") from last
