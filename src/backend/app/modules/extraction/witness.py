"""Module 2a — witness statement extraction with the Featherless LLM.

The LLM (OpenAI-compatible chat API) turns the free text into candidate
observations. Everything it says is then checked here before it becomes an
Observation:

* ``raw_text`` must be a verbatim quote from the statement; the ``line:<n>``
  locator is taken from where the quote actually is, not from the LLM
* vehicle ids are mapped by rules (red sedan/car -> V1, white SUV -> V2) and
  left null when the description is ambiguous
* confidence comes from hedging words in the quote, blended with the LLM's
  own certainty label
* clock times ("around 5:40", "quarter to six") become ``timestamp`` with a
  ``time_uncertainty_s``; ``t_rel_s`` stays null because the witness clock is
  not the sensor clock

With no FEATHERLESS_API_KEY this raises NotImplementedError and the pipeline
serves the witness fixture.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time, timezone
from pathlib import Path

import httpx

from app import config
from app.schemas import Evidence, Observation, ObservationSet, SourceRef, SourceType

PRODUCER_PREFIX = "extraction.witness/featherless"
TIMEOUT_S = 60.0

ACTIONS = ["moving", "braking", "accelerating", "no_braking", "entering_intersection", "collision",
           "post_collision_slide", "stopped", "signal_state"]

SYSTEM_PROMPT = f"""You extract structured facts from traffic-accident witness statements.
Return ONLY a JSON object, no prose, with this shape:
{{"observations": [{{
  "line": <int, the line number where the quote starts>,
  "raw_text": "<exact verbatim quote from the statement, copied character for character>",
  "vehicle_description": "<how the witness describes the vehicle, e.g. 'red sedan', or null>",
  "action": "<one of: {', '.join(ACTIONS)}>",
  "order": <int, order in which this happened according to the witness, 1 = first; null for timing remarks>,
  "clock_time": "<HH:MM 24h if the witness gives a clock time for the incident, else null>",
  "time_uncertainty_min": <number, how many minutes the clock time could be off, else null>,
  "attributes": {{"speed_kmh": <number or null>, "speed_qualifier": "<at least|about|...>",
                  "direction": "<northbound|southbound|eastbound|westbound or null>",
                  "approach": "<NB|SB|EB|WB or null>", "signal_state": "<green|yellow|red or null>",
                  "vehicles": ["<descriptions of every vehicle involved, for collisions>"]}},
  "certainty": "<certain|fairly_sure|unsure|guess>"
}}]}}
Rules: one observation per distinct claim; do not invent facts; keep hedging words in raw_text;
the evening is 17:00-19:00 (\"5:40 in the evening\" = 17:40, \"quarter to six\" = 17:45).
Omit attribute keys you have no value for."""

# hedge cue -> confidence ceiling it implies
HEDGES = [
    (r"\b(maybe|might|perhaps|not sure|guess|i don't know|i didn't (see|check))\b", 0.5),
    (r"\b(i think|i would say|i believe|probably|seemed|looked like|or maybe|kind of|sort of)\b", 0.55),
    (r"\b(about|around|roughly|approximately|at least|a few|some)\b", 0.6),
    (r"\b(fairly sure|pretty sure|quite sure)\b", 0.65),
]
CERTAIN_CUES = r"\b(i am sure|i'm sure|definitely|certainly|clearly|i saw)\b"
LLM_CERTAINTY = {"certain": 0.85, "fairly_sure": 0.65, "unsure": 0.5, "guess": 0.4}
DEFAULT_CONFIDENCE = 0.75

COLOURS = {"red": "V1", "maroon": "V1", "white": "V2"}
TYPES = {"V1": {"sedan", "car", "hatchback"}, "V2": {"suv", "jeep", "car", "vehicle", "van"}}
VEHICLE_WORDS = r"\b(car|cars|sedan|suv|van|truck|bus|bike|motorcycle|auto|lorry|vehicle|jeep|hatchback)\b"


def _chat(messages: list[dict]) -> str:
    """One chat completion against Featherless; returns the message content."""
    r = httpx.post(
        f"{config.FEATHERLESS_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {config.FEATHERLESS_API_KEY}"},
        json={"model": config.FEATHERLESS_WITNESS_MODEL, "messages": messages, "temperature": 0.0,
              "max_tokens": 2048},
        timeout=TIMEOUT_S,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response contains no JSON object")
    data = json.loads(text[start:end + 1])
    if isinstance(data, list):
        data = {"observations": data}
    return data


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


class _Statement:
    """The statement text with a whitespace-normalised view that maps back to line numbers."""

    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.flat = ""
        self.line_at: list[int] = []  # line number for each char of self.flat
        for n, line in enumerate(self.lines, start=1):
            part = _norm(line)
            if not part:
                continue
            if self.flat:
                self.flat += " "
                self.line_at.append(n)
            self.flat += part
            self.line_at.extend([n] * len(part))
        self.lower = self.flat.lower()

    def find(self, quote: str, hint_line: int | None) -> tuple[str, int] | None:
        """Verbatim (case/whitespace-insensitive) quote -> (text as written, line number)."""
        q = _norm(quote).strip(" \"'").rstrip(".").lower()
        if not q:
            return None
        hits = [m.start() for m in re.finditer(re.escape(q), self.lower)]
        if not hits:
            return None
        pos = min(hits, key=lambda p: abs(self.line_at[p] - hint_line)) if hint_line else hits[0]
        return self.flat[pos:pos + len(q)], self.line_at[pos]

    def header(self, key: str) -> str | None:
        for line in self.lines:
            m = re.match(rf"\s*{key}\s*:\s*(.+)", line, flags=re.I)
            if m:
                return m.group(1).strip()
        return None


def resolve_vehicle(description: str | None) -> str | None:
    """Map a witness description to V1/V2 only when colour and type both agree."""
    if not description:
        return None
    d = description.lower()
    ids = {vid for colour, vid in COLOURS.items() if re.search(rf"\b{colour}\b", d)}
    if len(ids) != 1:
        return None
    vid = ids.pop()
    types = set(re.findall(VEHICLE_WORDS, d))
    if types and not types & TYPES[vid]:
        return None
    return vid


def _confidence(raw: str, certainty: str | None) -> float:
    text = raw.lower()
    lex = DEFAULT_CONFIDENCE
    for pattern, ceiling in HEDGES:
        if re.search(pattern, text):
            lex = min(lex, ceiling)
    if lex == DEFAULT_CONFIDENCE and re.search(CERTAIN_CUES, text):
        lex = 0.85
    llm = LLM_CERTAINTY.get((certainty or "").lower())
    conf = lex if llm is None else 0.6 * lex + 0.4 * llm
    return round(min(max(conf, 0.05), 0.95), 2)


def _clock_timestamp(clock: str | None) -> datetime | None:
    if not clock:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(clock))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    ref = config.INCIDENT_REFERENCE_TIME
    return datetime.combine(ref.date(), time(int(m.group(1)), int(m.group(2))), tzinfo=ref.tzinfo)


def _clean_attributes(attrs: dict | None) -> dict:
    out = {}
    for k, v in (attrs or {}).items():
        if v in (None, "", [], {}):
            continue
        if k == "speed_kmh":
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
        if k == "vehicles" and isinstance(v, list):
            v = [resolve_vehicle(x) or x for x in v if isinstance(x, str)]
        out[k] = v
    return out


def extract_witness(evidence: Evidence) -> ObservationSet:
    """Vehicles, actions, relative ordering, approximate times, uncertainty."""
    if not config.FEATHERLESS_API_KEY:
        raise NotImplementedError("FEATHERLESS_API_KEY not set; witness extraction needs the LLM")

    path = Path(evidence.stored_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    file = evidence.filename or path.name
    stmt = _Statement(text)
    numbered = "\n".join(f"{n:>3}| {line}" for n, line in enumerate(stmt.lines, start=1))
    content = _chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Statement (line numbers before '|'):\n{numbered}"},
    ])
    items = _parse_json(content).get("observations") or []

    witness = (stmt.header("Witness") or "").split()
    tag = re.sub(r"[^a-z0-9]+", "", (witness[0] if witness else path.stem).lower()) or "w"
    warnings: list[str] = []
    candidates = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip().lower().replace(" ", "_")
        try:
            hint = int(item.get("line")) if item.get("line") is not None else None
        except (TypeError, ValueError):
            hint = None
        found = stmt.find(str(item.get("raw_text") or ""), hint)
        if not action or not found:
            warnings.append(f"{file}: dropped LLM claim {item.get('raw_text')!r} (not a verbatim quote "
                            f"or no action).")
            continue
        raw, line = found
        desc = item.get("vehicle_description") or None
        vid = resolve_vehicle(desc)
        if desc and vid is None:
            warnings.append(f"{file} line {line}: vehicle '{desc}' could not be mapped unambiguously.")
        attrs = _clean_attributes(item.get("attributes"))
        ts = _clock_timestamp(item.get("clock_time"))
        uncertainty = 0.0
        if ts is not None:
            attrs["clock_time"] = ts.strftime("%H:%M")
            try:
                uncertainty = abs(float(item.get("time_uncertainty_min"))) * 60
            except (TypeError, ValueError):
                uncertainty = 300.0  # witnesses round to the nearest 5 minutes
        try:
            order = int(item["order"]) if item.get("order") is not None else None
        except (TypeError, ValueError):
            order = None
        candidates.append({"i": i, "line": line, "raw": raw, "desc": desc, "vid": vid, "action": action,
                           "attrs": attrs, "ts": ts, "unc": uncertainty, "order": order,
                           "conf": _confidence(raw, item.get("certainty"))})

    # Re-number the witness's ordering as 1..n, ties broken by position in the text.
    ordered = sorted((c for c in candidates if c["order"] is not None),
                     key=lambda c: (c["order"], c["line"], c["i"]))
    for n, c in enumerate(ordered, start=1):
        c["attrs"]["order"] = n
    if candidates and not ordered:
        warnings.append(f"{file}: LLM gave no relative order for the events.")
    if candidates and not any(c["ts"] for c in candidates):
        warnings.append(f"{file}: no clock time given for the incident.")

    observations = [
        Observation(
            id=f"wit_{tag}_{n:02d}",
            source_type=SourceType.witness,
            source_ref=SourceRef(evidence_id=evidence.id, file=file, locator=f"line:{c['line']}"),
            timestamp=c["ts"],
            t_rel_s=None,
            time_uncertainty_s=c["unc"],
            vehicle_id=c["vid"],
            vehicle_description=c["desc"],
            action=c["action"],
            attributes=c["attrs"],
            confidence=c["conf"],
            raw_text=c["raw"],
        )
        for n, c in enumerate(sorted(candidates, key=lambda c: (c["line"], c["i"])), start=1)
    ]
    return ObservationSet(source_type=SourceType.witness, evidence_id=evidence.id,
                          producer=f"{PRODUCER_PREFIX}:{config.FEATHERLESS_WITNESS_MODEL}",
                          generated_at=datetime.now(timezone.utc), observations=observations,
                          warnings=warnings)
