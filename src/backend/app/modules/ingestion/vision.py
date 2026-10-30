"""Featherless vision model (OpenAI-compatible chat API): one dashcam frame -> FrameAnalysis."""
from __future__ import annotations

import json
import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

VEHICLE_ACTIONS = (
    "moving", "braking", "stopped", "turning", "accelerating",
    "entering_intersection", "crossing_intersection",
)
SIGNAL_STATES = ("red", "yellow", "green", "unknown")

SYSTEM_PROMPT = """You analyse single frames from a forward-facing dashcam for a traffic-incident investigation.
Report only what is visible. Answer with ONE JSON object and nothing else, using this shape:
{
  "ego": {"action": "moving|braking|stopped|turning|accelerating|entering_intersection|crossing_intersection",
          "cue": "what in the frame shows this", "confidence": 0.0-1.0} or null,
  "vehicles": [{"label": "short stable name, colour + type, e.g. 'white SUV'",
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


class VehicleSighting(_Lenient):
    label: str
    description: str | None = None
    action: str = "moving"
    direction: str | None = None
    confidence: float = 0.5

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, v):
        return _norm_action(v)


class SignalSighting(_Lenient):
    approach: str | None = None
    state: Literal["red", "yellow", "green", "unknown"] = "unknown"
    confidence: float = 0.5

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, v):
        s = str(v or "").strip().lower()
        s = {"amber": "yellow", "orange": "yellow"}.get(s, s)
        return s if s in SIGNAL_STATES else "unknown"


class CollisionSighting(_Lenient):
    visible: bool = False
    vehicles: list[str] = Field(default_factory=list)
    impact: str | None = None
    confidence: float = 0.5


class FrameAnalysis(_Lenient):
    ego: EgoState | None = None
    vehicles: list[VehicleSighting] = Field(default_factory=list)
    signals: list[SignalSighting] = Field(default_factory=list)
    collision: CollisionSighting | None = None


class VisionError(RuntimeError):
    """The model call failed or returned something unusable."""


def parse_frame_analysis(text: str) -> FrameAnalysis:
    """Pull the first JSON object out of the reply (tolerates code fences and prose) and validate it."""
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start = cleaned.find("{")
    if start < 0:
        raise VisionError(f"no JSON object in model reply: {text[:120]!r}")
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as e:
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

    def describe_frame(self, jpeg_b64: str, t_s: float, retries: int = 1) -> FrameAnalysis:
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Dashcam frame at t={t_s:.2f} s. Return the JSON object."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{jpeg_b64}"}},
                ]},
            ],
        }
        last: Exception | None = None
        for _ in range(retries + 1):
            try:
                r = self._http.post("/chat/completions", json=payload)
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
                return parse_frame_analysis(text)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, VisionError) as e:
                last = e
        raise VisionError(f"frame t={t_s:.2f}s: {type(last).__name__}: {last}") from last
