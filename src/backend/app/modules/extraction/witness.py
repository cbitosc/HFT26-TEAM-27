"""Module 2a — witness statement extraction with the Featherless LLM.

The LLM (OpenAI-compatible chat API) turns the free text into candidate
observations. Everything it says is then checked here before it becomes an
Observation:

* ``raw_text`` must be a verbatim quote from the statement; the ``line:<n>``
  locator is taken from where the quote actually is, not from the LLM
* vehicle ids come from the case's vehicle list (id, description, aliases): a description is
  linked only when its colour and type words match exactly one declared vehicle; otherwise it
  stays null and the fusion stage may link it across sources
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

SYSTEM_PROMPT = """You extract structured facts from traffic-accident witness statements.
Return ONLY a JSON object, no prose, with this shape:
{"observations": [{
  "line": <int, the line number where the quote starts>,
  "raw_text": "<exact verbatim quote from the statement, copied character for character>",
  "vehicle_description": "<how the witness describes the vehicle, e.g. 'red sedan', or null>",
  "action": "<one of: %(actions)s>",
  "order": <int, order in which this happened according to the witness, 1 = first; null for timing remarks>,
  "clock_time": "<HH:MM 24h if the witness gives a clock time for the incident, else null>",
  "time_uncertainty_min": <number, how many minutes the clock time could be off, else null>,
  "attributes": {"speed_kmh": <number or null>, "speed_qualifier": "<at least|about|...>",
                  "direction": "<northbound|southbound|eastbound|westbound or null>",
                  "approach": "<NB|SB|EB|WB or null>", "signal_state": "<green|yellow|red or null>",
                  "vehicles": ["<descriptions of every vehicle involved, for collisions>"]},
  "certainty": "<certain|fairly_sure|unsure|guess>"
}]}
Rules: one observation per distinct claim; do not invent facts; keep hedging words in raw_text;
%(time_context)s
Always extract, each as its own observation: every traffic-light colour mentioned (action "signal_state",
attributes.signal_state, vehicle_description = whose light it was), every speed estimate, every clock time
for the incident (action "collision" with clock_time), every vehicle description, including hedged ones.
Only give a direction if the statement states it (e.g. "from the south" = northbound); never guess.
A claim that a vehicle did NOT brake or slow down is action "no_braking". A speed estimate belongs to the
vehicle it describes, even inside a sentence about the crash.
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

COLOUR_WORDS = r"\b(red|maroon|white|black|silver|grey|gray|blue|green|yellow|orange|brown|gold|beige|purple|pink)\b"
VEHICLE_WORDS = (r"\b(car|sedan|suv|van|truck|lorry|bus|bike|motorbike|motorcycle|scooter|auto|rickshaw|"
                 r"jeep|hatchback|pickup|taxi|cab|tractor|cyclist|bicycle|pedestrian)\b")
GENERIC_TYPES = {"car", "vehicle"}  # compatible with any specific car-like type


def _words(text: str) -> tuple[set[str], set[str]]:
    t = (text or "").lower()
    colours = {("grey" if c == "gray" else c) for c in re.findall(COLOUR_WORDS, t)}
    return colours, set(re.findall(VEHICLE_WORDS, t))


def prompt() -> str:
    ref = config.INCIDENT_REFERENCE_TIME
    if config.REFERENCE_SOURCE == "default":
        ctx = ("Convert clock times to HH:MM 24h using the statement's own words (\"in the evening\", "
               "\"PM\"); if the time of day is ambiguous, give your best reading and lower certainty.")
    else:
        ctx = (f"The incident happened on {ref:%Y-%m-%d} at about {ref:%H:%M} local time; use this to resolve "
               "ambiguous clock times (e.g. AM vs PM) to HH:MM 24h.")
    return SYSTEM_PROMPT % {"time_context": ctx, "actions": ", ".join(ACTIONS)}


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


def resolve_vehicle(description: str | None, vehicles: list[dict] | None = None) -> str | None:
    """A declared vehicle id for a witness description, only when exactly one declared vehicle matches.

    Match: the declared id or an alias appears in the text, or the colours agree and the types are
    compatible ("car" fits any car-like type). With no declared vehicles nothing is resolved here.
    """
    if not description:
        return None
    vehicles = config.CASE_VEHICLES if vehicles is None else vehicles
    text = description.lower()
    by_name = [v["id"] for v in vehicles
               if any(re.search(rf"\b{re.escape(n.lower())}\b", text) for n in [v["id"], *v.get("aliases", [])])]
    if len(by_name) == 1:
        return by_name[0]
    colours, types = _words(description)
    if not colours:
        return None
    hits = []
    for v in vehicles:
        vc, vt = _words(v.get("description", ""))
        if colours & vc and (not types or not vt or (types | vt) & GENERIC_TYPES or types & vt):
            hits.append(v["id"])
    return hits[0] if len(hits) == 1 else None


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


CARDINAL_BOUND = {"north": "northbound", "south": "southbound", "east": "eastbound", "west": "westbound"}
OPPOSITE = {"north": "southbound", "south": "northbound", "east": "westbound", "west": "eastbound"}
BOUND_APPROACH = {"northbound": "NB", "southbound": "SB", "eastbound": "EB", "westbound": "WB"}


def grounded_direction(quote: str) -> str | None:
    """Direction of travel only if the quote itself states it ("from the south", "heading east", "northbound")."""
    q = quote.lower()
    if m := re.search(r"\b(north|south|east|west)bound\b", q):
        return CARDINAL_BOUND[m.group(1)]
    if m := re.search(r"\b(?:heading|going|driving|travell?ing)\s+(north|south|east|west)\b", q):
        return CARDINAL_BOUND[m.group(1)]
    if m := re.search(r"\bfrom the (north|south|east|west)\b", q):
        return OPPOSITE[m.group(1)]
    return None


NO_BRAKING = re.compile(r"\b(never|did ?n[o']?t|didn't|without|no)\s+(even\s+)?(slow(ed|ing)?|brak(e|ed|ing)|stop(ped)?\s+"
                        r"braking|break(ed)?)\b|\bkept (going|driving) at the same speed\b", re.I)


def infer_action(item: dict, attrs: dict) -> str | None:
    """The LLM sometimes leaves `action` empty for timing or signal remarks; recover it from what it did fill in."""
    action = str(item.get("action") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if action in ("", "moving", "braking", "null", "none", "unknown") and NO_BRAKING.search(str(item.get("raw_text") or "")):
        return "no_braking"  # "never slowed down", "did not brake": the words decide, whatever the model chose
    if action and action not in ("null", "none", "unknown"):
        return action
    if attrs.get("signal_state"):
        return "signal_state"
    if item.get("clock_time"):
        return "collision"  # "the crash happened at about quarter to six"
    if attrs.get("speed_kmh") is not None or item.get("vehicle_description"):
        return "moving"
    return None


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
        {"role": "system", "content": prompt()},
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
        attrs = _clean_attributes(item.get("attributes"))
        action = infer_action(item, attrs)
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
        # Directions must come from the words, not the model's guess ("from my left" says nothing).
        llm_dir = attrs.pop("direction", None)
        attrs.pop("approach", None)
        if direction := grounded_direction(raw):
            attrs["direction"] = direction
            attrs["approach"] = BOUND_APPROACH[direction]
        elif llm_dir:
            warnings.append(f"{file} line {line}: direction '{llm_dir}' dropped (not stated in the quote).")
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

    # A vehicle's direction stated once ("coming up Main Road from the south") applies to its other claims,
    # e.g. "its light was green" is about that vehicle's approach.
    known = {c["vid"]: c["attrs"]["approach"] for c in candidates if c["vid"] and c["attrs"].get("approach")}
    for c in candidates:
        if c["vid"] in known and "approach" not in c["attrs"]:
            c["attrs"]["approach"] = known[c["vid"]]
    # A speed estimate belongs to the vehicle it describes, even when the model attached it to a crash claim.
    for c in list(candidates):
        if c["action"] == "collision" and "speed_kmh" in c["attrs"] and c["vid"]:
            speed = {k: c["attrs"].pop(k) for k in ("speed_kmh", "speed_qualifier") if k in c["attrs"]}
            candidates.append({**c, "action": "moving", "attrs": speed | ({"approach": c["attrs"]["approach"]}
                                                                           if "approach" in c["attrs"] else {}),
                               "ts": None, "unc": 0.0, "i": c["i"] + 0.5})

    # A collision in a statement that names both vehicles involves both.
    involved = sorted({c["vid"] for c in candidates if c["vid"]})
    for c in candidates:
        both = c["action"] == "post_collision_slide" or re.search(r"\b(both|the two|each other)\b", c["raw"].lower())
        if (c["action"] == "collision" or both) and len(involved) >= 2:
            listed = [v for v in c["attrs"].get("vehicles", []) if isinstance(v, str)]
            if len({v for v in listed if v in involved}) < 2:
                c["attrs"]["vehicles"] = involved
                c["vid"] = None  # a collision belongs to both, not one

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
