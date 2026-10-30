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

MODEL = os.environ.get("FEATHERLESS_CONTRADICTIONS_MODEL", "Qwen/Qwen2.5-7B-Instruct")
TIMEOUT_S = 30.0

PROMPT = """You compare witness claims about a traffic collision with the measured record.
Report only claims that clearly contradict the record. Ignore claims that merely add detail.
Reply with JSON only: {"conflicts": [{"witness_observation_id": str, "record_observation_ids": [str],
"severity": "low"|"medium"|"high", "title": str, "description": str}]}

Witness claims (id: quote):
%s

Measured record (id: t_rel_s, vehicle, action, attributes):
%s
"""


def _summarise(ctx: Context, covered: set[str]) -> tuple[list[str], list[str]]:
    claims = [f"{o.id}: {o.raw_text}" for o in ctx.observations
              if o.source_type == SourceType.witness and o.raw_text and o.id not in covered]
    record = [f"{o.id}: {ctx.t_of(o)}, {o.vehicle_id}, {o.action}, {json.dumps(o.attributes)}"
              for o in ctx.timed(lambda o: o.source_type != SourceType.witness)]
    return claims, record


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
            json={"model": MODEL, "temperature": 0,
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

    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        wid = it.get("witness_observation_id")
        refs = [r for r in it.get("record_observation_ids") or [] if r in ctx.by_id]
        if wid not in ctx.by_id or wid in covered or not it.get("title"):
            continue  # drop hallucinated ids and anything the rules already flagged
        sev = it.get("severity") if it.get("severity") in {s.value for s in Severity} else "low"
        out.append({"type": ConflictType.semantic, "severity": Severity(sev), "title": str(it["title"])[:120],
                    "description": str(it.get("description") or ""), "observation_ids": [wid] + refs,
                    "t_range_s": ctx.window_of(ctx.by_id[wid]), "details": {"method": "llm_semantic", "model": MODEL}})
    return out
