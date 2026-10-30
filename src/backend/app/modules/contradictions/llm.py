"""Optional Featherless pass over free-text witness claims the rules did not cover.

Skipped (returns []) when FEATHERLESS_API_KEY is unset or the call fails in any
way, so the rule-based report always stands on its own.
"""
from __future__ import annotations

import json
import logging
import os

from app import config
from app.schemas import ConflictType, Severity, SourceType

from .context import Context

log = logging.getLogger(__name__)

MODEL = os.environ.get("FEATHERLESS_CONTRADICTIONS_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507")
TIMEOUT_S = 30.0
MAX_RECORD_LINES = 300  # keeps the prompt bounded for long logs

PROMPT = """You compare witness claims about a traffic collision with the measured record (sensors, cameras).
A claim CONTRADICTS the record only if the record shows the opposite of what the witness says.
If the record agrees with the claim, or simply says nothing about it, it is NOT a contradiction: leave it out.
For each contradiction, cite the record line ids that show the opposite. Most claims are not contradictions;
an empty list is a normal answer. Reply with JSON only:
{"conflicts": [{"witness_observation_id": str, "record_observation_ids": [str], "title": str,
"description": "what the witness says vs what the record shows, one or two sentences"}]}

Witness claims (id: quote):
%s

Measured record, per vehicle, in time order (id: what was measured):
%s
"""

# Witness action -> record actions that mean the same thing (agreement, not conflict).
_AGREES = {"braking": {"braking", "stopped"}, "stopped": {"stopped"}, "moving": {"moving"},
           "entering_intersection": {"entering_intersection"}, "collision": {"collision"},
           "post_collision_slide": {"stopped", "sudden_heading_change"}}


def _describe(ctx: Context, o) -> str:
    a = o.attributes
    bits = [o.action.replace("_", " ")]
    for key, fmt in (("speed_kmh", "{:g} km/h"), ("heading_deg", "heading {:g} deg"), ("signal_state", "light {}"), ("approach", "approach {}"),
                     ("heading_change_deg", "heading change {:+g} deg")):
        if a.get(key) is not None:
            bits.append(fmt.format(a[key]))
    if a.get("vehicles"):
        bits.append("vehicles " + "+".join(map(str, a["vehicles"])))
    return f"{o.id}: t={ctx.t_of(o):g} s, " + ", ".join(bits)


def _summarise(ctx: Context, covered: set[str]) -> tuple[list[str], list[str]]:
    claims = [f"{o.id}: {o.raw_text}" for o in ctx.observations
              if o.source_type == SourceType.witness and o.raw_text and o.id not in covered]
    record = ctx.timed(lambda o: o.source_type != SourceType.witness)
    if len(record) > MAX_RECORD_LINES:  # keep every event, thin out the repetitive "moving" samples
        keep = [o for o in record if o.action != "moving"]
        moving = [o for o in record if o.action == "moving"]
        step = max(1, len(moving) * 2 // max(1, MAX_RECORD_LINES))
        record = sorted(keep + moving[::step], key=lambda o: ctx.t_of(o))
    lines = []
    for who in sorted({o.vehicle_id or "" for o in record}):
        lines.append(f"[{who or 'scene / signals'}]")
        lines += [_describe(ctx, o) for o in record if (o.vehicle_id or "") == who]
    return claims, lines


def _record_agrees(ctx: Context, witness_obs) -> bool:
    """The record shows the same vehicle doing what the witness says: not a contradiction."""
    same = _AGREES.get(witness_obs.action)
    if not same or not witness_obs.vehicle_id and witness_obs.action != "collision":
        return False
    return any(o.source_type != SourceType.witness and o.action in same
               and (witness_obs.action == "collision" or o.vehicle_id == witness_obs.vehicle_id)
               for o in ctx.observations)


def semantic_pass(ctx: Context, covered: set[str]) -> list[dict]:
    if not config.FEATHERLESS_API_KEY:
        return []
    claims, record = _summarise(ctx, covered)
    if not claims:
        return []
    try:
        import httpx

        resp = httpx.post(
            f"{config.FEATHERLESS_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.FEATHERLESS_API_KEY}"},
            json={"model": MODEL, "temperature": 0, "max_tokens": 1500,
                  "messages": [{"role": "user", "content": PROMPT % ("\n".join(claims), "\n".join(record))}]},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        text = text[text.find("{"): text.rfind("}") + 1]
        items = json.loads(text).get("conflicts", [])
    except Exception as e:  # network, auth, bad JSON: the rules already ran
        log.warning("contradictions LLM pass skipped: %s: %s", type(e).__name__, e)
        return []

    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        wid = it.get("witness_observation_id")
        refs = [r for r in it.get("record_observation_ids") or []
                if r in ctx.by_id and ctx.by_id[r].source_type != SourceType.witness]
        if wid not in ctx.by_id or wid in covered or wid in seen or not refs or not it.get("title"):
            continue  # hallucinated ids, no cited record, already flagged by a rule, or a repeat
        if _record_agrees(ctx, ctx.by_id[wid]):
            log.info("LLM conflict on %s dropped: the record agrees with the claim", wid)
            continue
        seen.add(wid)
        # Unverified by a rule, so never "high": the investigator should check it.
        out.append({"type": ConflictType.semantic, "severity": Severity.medium if len(refs) > 1 else Severity.low,
                    "title": str(it["title"])[:120], "description": str(it.get("description") or ""),
                    "observation_ids": [wid] + refs, "t_range_s": ctx.window_of(ctx.by_id[wid]),
                    "details": {"method": "llm_semantic", "model": MODEL, "verified_by_rule": False}})
    return out
