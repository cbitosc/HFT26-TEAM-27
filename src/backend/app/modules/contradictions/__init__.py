"""Module 4 — Contradiction detection.

Rule-based numeric comparisons, timestamp overlap checks, LLM-assisted
semantic comparison (optional), missing-evidence detection. Emits conflicts.json.
"""
from datetime import datetime, timezone

from app.schemas import Conflict, ConflictReport, ObservationSet, Severity, SourceType, Timeline

from .context import Context
from .llm import semantic_pass
from .rules import ALL_RULES

_SEVERITY_ORDER = {Severity.high: 0, Severity.medium: 1, Severity.low: 2}


def detect_conflicts(observation_sets: list[ObservationSet], timeline: Timeline) -> ConflictReport:
    ctx = Context.build(observation_sets, timeline)
    drafts = [d for rule in ALL_RULES for d in rule(ctx)]

    # The LLM only sees witness claims no rule has already flagged, including other observations
    # extracted from the same sentence (one quote can yield a signal claim and an "accelerating" claim).
    flagged = {i for d in drafts for i in d["observation_ids"][:1] if ctx.by_id[i].source_type == SourceType.witness}
    quotes = {ctx.by_id[i].raw_text for i in flagged if ctx.by_id[i].raw_text}
    covered = flagged | {o.id for o in ctx.observations
                         if o.source_type == SourceType.witness and o.raw_text and o.raw_text in quotes}
    drafts += semantic_pass(ctx, covered)

    drafts.sort(key=lambda d: (_SEVERITY_ORDER[d["severity"]],
                               d["t_range_s"][0] if d["t_range_s"] else float("inf")))
    return ConflictReport(
        incident_id=timeline.incident_id,
        generated_at=datetime.now(timezone.utc),
        conflicts=[Conflict(id=f"cf_{i:02d}", **d) for i, d in enumerate(drafts, 1)],
    )
