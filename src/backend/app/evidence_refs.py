"""Human-readable pointers from an observation back into its original evidence ("EVID-002, line 3")."""
from __future__ import annotations

from app.schemas import Evidence, ObservationSet


def locator_text(locator: str) -> str:
    """`line:3` -> "line 3", `rows:412-415` -> "rows 412-415", `frames:25-56` -> "frames 25-56"."""
    kind, _, rest = locator.partition(":")
    return f"{kind} {rest}" if rest else locator


def stamp_evidence_refs(observation_sets: list[ObservationSet], evidence: list[Evidence]) -> None:
    """Set `evidence_ref` on every observation, in place. Evidence the index does not know (bundled demo
    fixtures) keeps its raw evidence id, so the pointer never claims an id that does not exist."""
    refs = {e.id: e.ref for e in evidence if e.ref}
    for s in observation_sets:
        for o in s.observations:
            ref = o.source_ref
            o.evidence_ref = f"{refs.get(ref.evidence_id, ref.evidence_id)}, {locator_text(ref.locator)}"
