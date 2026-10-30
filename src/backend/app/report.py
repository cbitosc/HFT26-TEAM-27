"""Incident report: one document an investigator can hand over, built from a run's outputs.

Everything factual is composed deterministically from the run (timeline, conflicts,
reconstruction, observation warnings, evidence manifest, audit log), with a citation
(file @ line/row/frame) for every claim. When a Featherless key is set, a short narrative
summary is drafted by the LLM from those same facts only, labelled as AI-drafted, and
dropped if it mentions a time that is not in the facts.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import httpx

from app import config, store
from app.schemas import (AnalysisRun, ConflictReport, IncidentReport, Observation, ObservationSet, Reconstruction,
                         Severity, Timeline)

log = logging.getLogger(__name__)

SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "⚪"}
STATUS_NOTE = {"skipped": "not run", "error": "failed", "fixture": "demo data"}


def _cite(o: Observation | None) -> str:
    return f"{o.source_ref.file} @ {o.source_ref.locator}" if o else "?"


def _fmt_t(t: float | None) -> str:
    return "—" if t is None else f"{t:g} s"


def _clock(t: float | None) -> str:
    if t is None or not config.REFERENCE_TIME_CONFIRMED:
        return "—"  # without a confirmed reference time a wall-clock time would be invented
    from datetime import timedelta
    return (config.INCIDENT_REFERENCE_TIME + timedelta(seconds=t)).strftime("%H:%M:%S")


def _load(run_id: str | None):
    get = (lambda n: store.load_run_output(run_id, n)) if run_id else store.load_output
    raw = {n: get(f"{n}.json") for n in ("run", "observations", "timeline", "conflicts", "reconstruction")}
    if raw["run"] is None:
        return None
    return (AnalysisRun.model_validate(raw["run"]),
            [ObservationSet.model_validate(s) for s in raw["observations"] or []],
            Timeline.model_validate(raw["timeline"]), ConflictReport.model_validate(raw["conflicts"]),
            Reconstruction.model_validate(raw["reconstruction"]))


def _facts(run, sets, timeline, conflicts, recon) -> dict:
    """The structured facts the report (and the optional narrative) are built from."""
    by_id = {o.id: o for s in sets for o in s.observations}
    collision = next((e for e in timeline.events if e.category == "collision"), None)
    counts = {s.value: sum(c.severity == s for c in conflicts.conflicts) for s in Severity}
    not_run = [s for s in run.stages if s.status in ("skipped", "error")]
    return {
        "incident_id": timeline.incident_id, "run_id": run.run_id,
        "reference_time": timeline.reference_time.isoformat(),
        "evidence_count": run.evidence_count,
        "collision": None if not collision else {
            "t_s": collision.t_start_s, "clock": _clock(collision.t_start_s), "vehicles": collision.vehicle_ids,
            "sources": [s.value for s in collision.source_types], "confidence": collision.confidence,
            "where": None if not recon.collision else [recon.collision.x_m, recon.collision.y_m]},
        "vehicles": [{"id": v.vehicle_id, "label": v.label, "description": v.description} for v in recon.vehicles],
        "events": [{"t_s": e.t_start_s, "label": e.label, "sources": [s.value for s in e.source_types],
                    "confidence": e.confidence} for e in timeline.events],
        "conflicts": [{"severity": c.severity.value, "type": c.type.value, "title": c.title,
                       "description": c.description,
                       "citations": [_cite(by_id.get(i)) for i in c.observation_ids]} for c in conflicts.conflicts],
        "conflict_counts": counts,
        "not_run": [{"stage": s.stage, "status": s.status, "why": s.message} for s in not_run],
    }


def _headline(f: dict) -> str:
    c = f["collision"]
    parts = []
    if c:
        who = " and ".join(c["vehicles"]) or "vehicles"
        when = f" ({c['clock']})" if c["clock"] != "—" else ""
        parts.append(f"Collision between {who} at t={c['t_s']:g} s{when}, "
                     f"supported by {', '.join(c['sources'])} evidence")
    else:
        parts.append("No collision established from the evidence")
    n = f["conflict_counts"]
    if sum(n.values()):
        parts.append(f"{n['high']} high, {n['medium']} medium and {n['low']} low-severity contradiction(s)")
    else:
        parts.append("no contradictions between sources")
    return "; ".join(parts) + "."


SUMMARY_PROMPT = """Write a neutral 3-5 sentence summary of a traffic-incident analysis for an investigator.
Use ONLY the facts in the JSON below. Do not add causes, blame, speeds or times that are not in it.
Mention the most important contradictions and evidence gaps. When you mention a contradiction, attribute
each claim exactly as its "description" does (who said what, what the record shows); never merge or swap
claims between witnesses. Plain prose, no lists, no headings.

Facts:
%s"""


def _llm_summary(f: dict) -> str | None:
    if not config.FEATHERLESS_API_KEY:
        return None
    try:
        r = httpx.post(f"{config.FEATHERLESS_BASE_URL.rstrip('/')}/chat/completions",
                       headers={"Authorization": f"Bearer {config.FEATHERLESS_API_KEY}"},
                       json={"model": config.FEATHERLESS_WITNESS_MODEL, "temperature": 0, "max_tokens": 400,
                             "messages": [{"role": "user", "content": SUMMARY_PROMPT % json.dumps(f, default=str)}]},
                       timeout=45)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("report summary skipped: %s: %s", type(e).__name__, e)
        return None
    # Guard against invented numbers: every time-like figure must appear in the facts.
    facts_text = json.dumps(f, default=str)
    for num in re.findall(r"\b\d{1,2}:\d{2}(?::\d{2})?\b|\b\d+(?:\.\d+)? s\b", text):
        if num.replace(" s", "") not in facts_text:
            log.warning("report summary dropped: '%s' is not in the facts", num)
            return None
    return text


def build_report(run_id: str | None = None, narrative: bool = True) -> IncidentReport | None:
    loaded = _load(run_id)
    if loaded is None:
        return None
    run, sets, timeline, conflicts, recon = loaded
    f = _facts(run, sets, timeline, conflicts, recon)
    by_id = {o.id: o for s in sets for o in s.observations}
    summary = _llm_summary(f) if narrative else None
    headline = _headline(f)

    title = f" — {config.INCIDENT_TITLE}" if config.INCIDENT_TITLE else ""
    ref = (f"reference time {timeline.reference_time:%Y-%m-%d %H:%M:%S %z}" if config.REFERENCE_TIME_CONFIRMED
           else "reference time **not set** (times are seconds from the start of the evidence; set it with PUT /api/incident)")
    md: list[str] = [f"# Incident report — {timeline.incident_id}{title}", "",
                     *( [f"Location: {config.INCIDENT_LOCATION}", ""] if config.INCIDENT_LOCATION else []),
                     f"Run `{run.run_id}` · analysed {run.finished_at:%Y-%m-%d %H:%M} UTC · {ref} · "
                     f"report generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
                     f"**{headline}**", ""]
    if summary:
        md += ["> **AI-drafted summary** (from the facts below; verify against the cited evidence)", ">",
               *[f"> {line}" for line in summary.splitlines()], ""]

    md += ["## Evidence", "", "| File | Type | SHA-256 | Analysis |", "|---|---|---|---|"]
    status_by_file = {}
    for s in run.stages:
        m = re.search(r"\[(.+)\]$", s.stage)
        if m:
            status_by_file[m.group(1)] = STATUS_NOTE.get(s.status, "analysed") + (f": {s.message}" if s.status in ("skipped", "error") else "")
    kinds = {e.id: e.kind.value for e in store.list_evidence()}
    for e in run.evidence:
        md.append(f"| {e.filename} | {kinds.get(e.id, '—')} | `{(e.sha256 or '—')[:16]}…` | {status_by_file.get(e.filename, '—')} |")
    if not run.evidence:
        md.append("| (demo fixtures — no evidence uploaded) | | | |")
    md.append("")

    md += ["## Timeline", "", "| t | Clock | Event | Sources | Confidence |", "|---|---|---|---|---|"]
    for e in timeline.events:
        md.append(f"| {_fmt_t(e.t_start_s)}{'–' + _fmt_t(e.t_end_s) if e.t_end_s != e.t_start_s else ''} "
                  f"| {_clock(e.t_start_s)} | {e.label} | {', '.join(s.value for s in e.source_types)} "
                  f"| {e.confidence:.0%} |")
    if not timeline.events:
        md.append("| | | No events could be placed in time. | | |")
    md.append("")

    md += ["## Contradictions", ""]
    if not conflicts.conflicts:
        md += ["No contradictions between sources.", ""]
    for c in conflicts.conflicts:
        verified = "" if c.details.get("method") != "llm_semantic" else " *(LLM-detected, not rule-verified)*"
        md += [f"### {SEVERITY_ICON[c.severity.value]} {c.title} — {c.severity.value}, {c.type.value.replace('_', ' ')}{verified}",
               "", c.description, ""]
        for i in c.observation_ids:
            o = by_id.get(i)
            quote = f' — "{o.raw_text}"' if o and o.raw_text else ""
            md.append(f"- `{i}` {_cite(o)}{quote}")
        md.append("")

    gaps = [c for c in conflicts.conflicts if c.type.value == "missing_evidence"]
    md += ["## Evidence gaps and limitations", ""]
    for s in f["not_run"]:
        md.append(f"- **{s['stage']}** {STATUS_NOTE.get(s['status'], s['status'])}: {s['why']}")
    for c in gaps:
        md.append(f"- {c.title}")
    notes = [(s.producer, w) for s in sets for w in s.warnings]
    for producer, w in notes:
        md.append(f"- *{producer}*: {w}")
    if not (f["not_run"] or gaps or notes):
        md.append("- None recorded.")
    md.append("")

    md += ["## Reconstruction", ""]
    if recon.vehicles and recon.scene.type == "camera_image":
        moving = [v for v in recon.vehicles if v.vehicle_id.startswith("T")]
        still = [v for v in recon.vehicles if v.vehicle_id.startswith("S")]
        per_view = {}
        for v in moving:
            per_view[v.vehicle_id[1:].split("-")[0]] = per_view.get(v.vehicle_id[1:].split("-")[0], 0) + 1
        md.append(f"{len(moving)} moving-vehicle trajectories tracked on the camera image over "
                  f"t={recon.t_min_s:g}–{recon.t_max_s:g} s ("
                  + ", ".join(f"view {k}: {n}" for k, n in sorted(per_view.items())) + ").")
        track_obs = [o for o in by_id.values() if o.action == "track"]
        md += ["", "| View | Tracks | Calibration | Speed km/h (10th / median / 90th pct) |", "|---|---|---|---|"]
        notes = {int(m.group(1)): w for s in sets for w in s.warnings
                 if (m := re.match(r"Camera view (\d+) (?:calibrated|not calibrated)", w))}
        for view in sorted(int(k) for k in per_view):
            speeds = sorted(o.attributes["mean_speed_kmh"] for o in track_obs
                            if o.attributes.get("view") == view and o.attributes.get("mean_speed_kmh") is not None)
            cal = notes.get(view, "—").split(": ", 1)[-1].rstrip(".")
            if speeds:
                q = lambda f: speeds[min(len(speeds) - 1, int(f * len(speeds)))]  # noqa: E731
                stat = f"{q(0.1):.0f} / {q(0.5):.0f} / {q(0.9):.0f} (n={len(speeds)})"
            else:
                stat = "—"
            md.append(f"| {view} | {per_view[str(view)]} | {cal} | {stat} |")
        md.append("")
        for v in still:
            md.append(f"- Stationary: {v.label}, t={v.track[0].t_s:g}–{v.track[-1].t_s:g} s, at pixel "
                      f"({v.track[0].x_m:g}, {v.track[0].y_m:g}) — {', '.join(_cite(by_id.get(i)) for i in v.source_observation_ids)}")
    elif recon.vehicles:
        md.append(f"Tracks for {', '.join(v.label for v in recon.vehicles)} over t={recon.t_min_s:g}–{recon.t_max_s:g} s.")
        if recon.collision:
            md.append(f"Collision point ({recon.collision.x_m:g} m, {recon.collision.y_m:g} m) at t={recon.collision.t_s:g} s, "
                      f"confidence {recon.collision.confidence:.0%}, supported by "
                      f"{', '.join(_cite(by_id.get(i)) for i in recon.collision.supporting_observation_ids)}.")
    else:
        md.append("No vehicle trajectories: the evidence contains no positional sensor tracks.")
    md.append("")

    md += ["## Chain of custody", ""]
    shas = {e.sha256 for e in run.evidence if e.sha256}
    events = [a for a in reversed(store.read_audit(5000))
              if a.data.get("sha256") in shas or a.data.get("run_id") == run.run_id]
    for a in events:
        what = a.data.get("filename") or a.data.get("run_id")
        md.append(f"- {a.at:%Y-%m-%d %H:%M:%S} UTC · {a.event.replace('_', ' ')} · {what}"
                  + (f" · sha256 `{a.data['sha256'][:16]}…`" if a.data.get("sha256") else ""))
    if not events:
        md.append("- No audit entries for this run.")
    md.append("")
    md += ["---", "*Generated by the incident reconstruction pipeline. Times are seconds from the reference time; "
           "positions are metres from the junction centre (+x east, +y north).*"]

    return IncidentReport(run_id=run.run_id, incident_id=timeline.incident_id, generated_at=datetime.now(timezone.utc),
                          headline=headline, summary=summary, facts=f, markdown="\n".join(md))
