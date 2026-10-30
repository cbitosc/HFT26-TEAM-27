# Integration contract

**Source of truth:** [`src/backend/app/schemas.py`](../../src/backend/app/schemas.py) (schema version `1.0.0`).
JSON Schema copies live in [`schemas/`](schemas/). Regenerate them with `python scripts/export_schemas.py`.
Example payloads for every shape are in [`/fixtures`](../../fixtures).

## Data flow

```
upload ──► Evidence ──► [1] analyze_video    ─┐
                    ──► [2] extract_witness  ─┼─► list[ObservationSet]
                    ──► [2] extract_sensor   ─┘          │
                                                         ▼
                                     [3] build_timeline ──► Timeline
                                                         │
                         [4] detect_conflicts(obs, timeline) ──► ConflictReport
                                                         │
       [5] build_reconstruction(obs, timeline, conflicts) ──► Reconstruction
                                                         │
                                             [6] Dashboard (React) via /api
```

## Shared conventions

| Concept | Rule |
| --- | --- |
| Time | `t_rel_s` = seconds from the incident reference time (`Timeline.reference_time`). Absolute times are ISO-8601 with an offset (the demo uses IST, `+05:30`). Unknown → `null`, never a guess. |
| Uncertainty | `time_uncertainty_s` / `uncertainty_s` = ± seconds. `confidence` is 0–1. |
| Space | Metres, origin = intersection centre, +x east, +y north. `heading_deg`: 0 = north, clockwise. |
| Vehicle ids | Canonical `V1`, `V2`, … Witness/video observations that can't be resolved use `vehicle_id: null` and put the text in `vehicle_description`. |
| Speed | Always `attributes.speed_kmh`. Keep the raw value in `speed_<unit>_raw` if you converted. |
| Source refs | Every observation has `source_ref.locator`: `frame:<n>` (video), `line:<n>` (witness), `row:<n>` / `rows:<a>-<b>` (CSV, 1-based incl. header). |
| Ids | Observations `vid_*`, `wit_*`, `sen_*`, `sig_*`; events `evt_*`; conflicts `cf_*`. Unique within a run. |
| Actions | Free string, but prefer: `moving`, `braking`, `accelerating`, `no_braking`, `entering_intersection`, `collision`, `post_collision_slide`, `stopped`, `signal_state`, `sudden_heading_change`, `sensor_gap`. Add new ones to this list. |

## Shapes (summary — see schemas.py for every field)

- **Observation** — `id, source_type, source_ref{evidence_id,file,locator}, timestamp, t_rel_s, time_uncertainty_s, vehicle_id, vehicle_description, action, attributes{}, confidence, raw_text`
- **ObservationSet** — one extractor run on one Evidence: `source_type, evidence_id, producer, generated_at, observations[], warnings[]`
- **Timeline** — `incident_id, reference_time, events[]`; each **TimelineEvent** has `t_start_s, t_end_s, label, category, vehicle_ids, observation_ids, source_types, confidence, uncertainty_s`
- **ConflictReport** — `conflicts[]`; each **Conflict** has `type ∈ {numeric_mismatch, timestamp_overlap, semantic, missing_evidence}`, `severity ∈ {low, medium, high}`, `title, description, observation_ids, t_range_s, details{}`
- **Reconstruction** — `scene{extent_m, lane_width_m, roads}, t_min_s, t_max_s, vehicles[{vehicle_id, label, color, track[{t_s,x_m,y_m,heading_deg,speed_kmh}]}], collision{t_s,x_m,y_m,confidence,supporting_observation_ids}`

## HTTP API (FastAPI, port 8000)

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/health` | status, schema version |
| GET / POST | `/api/evidence` | list / upload (`multipart file`). Type is picked by extension: `.mp4 .mov .avi .mkv` → video, `.txt` → witness, `.csv` → sensor |
| DELETE | `/api/evidence/{id}` | 204 |
| POST | `/api/analysis/run` | `AnalysisRun` with per-stage status `real \| fixture \| error` |
| GET | `/api/analysis/latest` | last `AnalysisRun` or `null` |
| GET | `/api/observations` | `ObservationSet[]` |
| GET | `/api/timeline` | `Timeline` |
| GET | `/api/conflicts` | `ConflictReport` |
| GET | `/api/reconstruction` | `Reconstruction` |

Before the first run, the GET endpoints serve fixtures.

## Changing the contract

1. Additive changes (a new optional field, a new action string) are fine. Bump the patch version and update the fixtures.
2. Renaming or removing a field, or changing a type, is a breaking change. Agree on it with the team, bump the minor version, and update `schemas.py`, the fixtures, `scripts/export_schemas.py` output and the frontend in **one** commit.
3. `pytest` must pass (`tests/test_fixtures.py` validates every fixture against the models).
