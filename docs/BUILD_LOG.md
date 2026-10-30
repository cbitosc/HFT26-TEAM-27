# Build log

Every change to the repo, newest at the bottom. Each entry says what was done, which
files changed, and why. Agents and team members append here (see CLAUDE.md).

Guides: `flow.pdf` (build order) and `architecture.pdf` (6 modules).

---

## 2026-10-30 · Walking skeleton (Claude Code session)

Built the flow from `flow.pdf` up to and including "walking skeleton". The last step,
"parallel Claude agents to build", has **not** started. Nothing is committed yet; all
files are untracked on `main`.

### Flow step 1: Find data (sensor CSVs via scripts, witness statements)

Synthetic incident `INC-2026-1012-01`: a red sedan (V1, northbound) and a white SUV
(V2, eastbound) collide at the Main Rd × 5th Ave junction at t=10.0 s, 17:42:10 IST.
V1 entered on a late yellow and was braking. V2 entered on red.

| File | What |
| --- | --- |
| `scripts/scenario.py` | Ground truth: vehicle motion profiles, signal plan, collision point, V1 telematics gap (6.0–7.5 s). Coordinates are metres, +x east, +y north, left-hand traffic. |
| `scripts/generate_sensor_data.py` | Writes three CSVs in deliberately different formats so normalisation has real work to do |
| `data/sensor/v1_telematics.csv` | ISO timestamps, km/h, 2 Hz. Rows for t=6.5 and 7.0 s are **missing** on purpose |
| `data/sensor/v2_obd.csv` | epoch ms, **mph**, unit id `OBD-7731` (not "V2"), brake as ON/OFF |
| `data/sensor/signal_controller.csv` | Phase-change log: NB green → yellow (t=7) → red (t=10); EB red → green (t=11) |
| `scripts/generate_witness_statements.py` | Writes witness statements and lists `KNOWN_DISCREPANCIES` as a test oracle |
| `data/witness/witness_01_pedestrian.txt` | W1 claims V1 had a green and V2 was doing "at least 80 km/h" (truth: 57.6) |
| `data/witness/witness_02_shopkeeper.txt` | W2 claims V1 "sped up" (truth: braking) and gives the time as 17:45 (truth: 17:42:10). Unsure whether V2 was an SUV or a van |
| `data/video/README.md` | Placeholder. No dashcam clip exists for the synthetic scenario yet |

### Flow step 2: Repo format

Follows the README's recommended `src/ docs/ tests/` layout:
`src/backend` (FastAPI), `src/frontend` (React), `fixtures/`, `data/`, `scripts/`,
`docs/contracts/`, `tests/`. Also added:

- `.gitignore`: `.venv/`, `node_modules/`, `dist/`, `storage/` (runtime uploads and outputs), `.env`
- `pytest.ini`: `pythonpath = src/backend`, so tests import `app.*` from the repo root
- `src/backend/requirements.txt`: pinned to the versions installed in `.venv` (Python 3.14): fastapi 0.162.0, pydantic 2.14.2, pandas 3.1.0, numpy 2.5.4, opencv-python-headless 5.0.0.93, ...
- `.claude/launch.json`: `backend` (uvicorn :8000) and `dashboard` (vite :5173) launch configs

### Flow step 3: Integration contract

| File | What |
| --- | --- |
| `src/backend/app/schemas.py` | **The contract.** Pydantic models: `Evidence`, `Observation`/`ObservationSet` (the common schema for video, witness and sensor), `Timeline`/`TimelineEvent`, `ConflictReport`/`Conflict` (4 types from the architecture), `Reconstruction` (scene, tracks, collision), `AnalysisRun`/`StageResult`. Schema version 1.0.0 |
| `docs/contracts/INTEGRATION_CONTRACT.md` | Data-flow diagram, shared conventions (time, space, ids, units, locators), the HTTP API table, and the rules for changing the contract |
| `scripts/export_schemas.py` → `docs/contracts/schemas/*.json` | JSON Schema export of the six top-level models, for the frontend and other non-Python consumers |

### Flow step 4: Hand-off contract

| File | What |
| --- | --- |
| `docs/contracts/HANDOFF_CONTRACT.md` | Module → owned folder → exact function signature → fixture it replaces → definition of done (taken from architecture.pdf), plus the rules for inputs, outputs, failures, LLM keys and tests |
| `src/backend/app/modules/{ingestion,extraction,timeline,contradictions,reconstruction}/__init__.py` | One stub per module with the agreed signature. Each raises `NotImplementedError` |

### Flow step 5: Fixtures

Hand-picked outputs for every module, consistent with `scenario.py` and with the line and
row numbers in `data/`. `scripts/author_fixtures.py` is the authoring aid: it holds the
picked content and computes only the coordinates and speeds.

| Fixture | Contents |
| --- | --- |
| `fixtures/video_observations.json` | 6 observations from V1's dashcam (frame = t×30): NB yellow at t=7.2, V2 enters at 9.1, impact at 10.0, ... |
| `fixtures/witness_observations.json` | 12 observations from W1 and W2 with verbatim `raw_text`, `line:` refs, `attributes.order`, and hedged confidence |
| `fixtures/sensor_observations.json` | 14 observations: V1/V2 motion, braking, impact heading changes, stops, signal phases, plus a `sensor_gap` observation for the V1 dropout |
| `fixtures/timeline.json` | 11 events that correlate observations across sources |
| `fixtures/conflicts.json` | 7 conflicts covering all 4 types: W1 speed (numeric), W1 green light and W2 "sped up" and V2 vehicle type (semantic), W2's 17:45 (timestamp overlap), V1 dropout and no view of V2's signal (missing evidence) |
| `fixtures/reconstruction.json` | Predefined intersection (extent 60 m, 3.5 m lanes), tracks for V1 and V2 at 0.5 s steps for t=5–13 s, collision marker at (-1.75, 1.75), t=10 |

### Flow step 6: CLAUDE.md

`CLAUDE.md`: project summary, layout, commands, and rules for agents (stay in your module
folder, don't edit fixtures to pass tests, `scenario.py` is the oracle, log to this file,
tests and build must pass before hand-off).

### Flow step 7: Walking skeleton (FastAPI returns fixtures, dashboard renders them)

Backend (`src/backend/app/`):

| File | What |
| --- | --- |
| `main.py` | FastAPI app, CORS for :5173, `/api/health` |
| `config.py` | Paths (fixtures, `storage/`), allowed extensions, 200 MB upload limit, Featherless env vars |
| `store.py` | File-backed evidence index, fixture loader, run-output persistence in `storage/runs/latest/` |
| `pipeline.py` | Runs modules 1→5 in architecture order. Each stage calls the real module first. On `NotImplementedError` it serves the fixture (`status: fixture`); on any other exception it serves the fixture with `status: error`. **Integration becomes a series of swaps: a module goes live as soon as it stops raising.** With no uploads it runs in demo mode on all three observation fixtures |
| `routers/evidence.py` | `POST/GET /api/evidence`, `DELETE /api/evidence/{id}`. Validates by extension (415 on unknown), rejects empty files (400) and files too large (413) |
| `routers/analysis.py` | `POST /api/analysis/run`, `GET /api/analysis/latest`, `GET /api/{observations,timeline,conflicts,reconstruction}`. Serves fixtures before the first run |

Frontend (`src/frontend/`, scaffolded with `create-vite` react template, React 19, Vite 8, Tailwind 4):

| File | What |
| --- | --- |
| `vite.config.js` | Tailwind plugin; proxies `/api` to `127.0.0.1:8000` |
| `src/api.js` | fetch wrapper for every endpoint |
| `src/App.jsx` | Loads all results and lays out the 4 dashboard panels. Shares the current time `t` and the selected observations across panels |
| `src/components/EvidenceManager.jsx` | Upload (multi-file), list, remove, **Run analysis**, per-stage `real / fixture / error` badges |
| `src/components/IncidentTimeline.jsx` | Gantt-style event rows coloured by category, opacity = confidence, source badges, ± uncertainty, a red cursor at the current `t`. Click an event to jump there |
| `src/components/ContradictionExplorer.jsx` | Conflict cards with severity borders and a type filter. Expanding a card shows the referenced observations with file, locator and verbatim quote, and jumps the timeline and viewer |
| `src/components/ReconstructionViewer.jsx` | SVG intersection, interpolated vehicle positions with heading and speed, trails, collision marker once `t ≥ t_collision`, play/pause and a time slider |
| `src/components/Panel.jsx` | Shared panel chrome and source badges |
| Removed | create-vite demo files `src/App.css`, `src/assets/*`, `public/icons.svg`. Page title set to "Incident Reconstruction" |

Tests (`tests/`):

- `conftest.py`: each test gets an isolated temp `storage/`
- `test_fixtures.py`: every fixture validates against the models; observation ids are unique; timeline, conflicts and collision only reference known observations; all 4 conflict types are present; tracks cover `[t_min, t_max]`
- `test_api.py`: health, fixtures served before any run, a demo run gives 6 stages all `fixture`, the upload → list → run → delete round trip, 415 on an unknown file type

### Verification

- `pytest`: **12 passed**
- `npm run build`: OK. `oxlint`: 2 style warnings left (a constants export in `Panel.jsx`, setState in the load effect in `App.jsx`). Neither affects behaviour
- Both servers started from `.claude/launch.json` and the dashboard was checked in the browser. "Run analysis" shows 6 stages as `fixture`. Clicking the collision event moves the viewer to t=10 s, and both vehicles meet at the collision marker. The contradiction panel lists all 7 conflicts

### Adjustments made during the build

- Scenario clock changed from UTC to IST (+05:30) so the witness wall-clock times read naturally (W1 "around 5:40", W2 "quarter to six")
- Witness fixture `line:` refs corrected to match the generated text files
- Reconstruction window now starts at t=5 s instead of 0. At t=0 both vehicles are more than 120 m out and invisible in a 120 m view. The dashboard clamps its starting time to `t_min_s`

### Not done yet (next flow step: parallel agents)

- Modules 1–5 are still stubs. Each is ready for one parallel agent, per `docs/contracts/HANDOFF_CONTRACT.md`
- No real dashcam video in `data/video/`
- Nothing is committed. The README's workflow suggests a `feature/prototype` branch

## 2026-10-30 · Skeleton committed

- Created branch `feature/prototype` from `main` and committed everything above as one commit. Runtime `storage/`, `.venv/` and `node_modules/` are ignored
- Next: one parallel session per module (1, 2, 3, 4, 5), each branching from `feature/prototype`

## 2026-10-30 · Module 1 (evidence ingestion) implemented

`analyze_video(evidence)` in `src/backend/app/modules/ingestion/` is now real (signature unchanged; an optional `client=` argument lets tests inject a fake model).

- `video.py`: OpenCV `probe_video` validates the file (opens, fps in (0, 1000], frame count > 0, first frame decodes; else `VideoError`). `sample_indices` picks frames at `VIDEO_SAMPLE_FPS` (default 3) capped at `VIDEO_MAX_FRAMES` (default 90). `extract_frames` reads them sequentially. Frames are downscaled to 768 px wide JPEG for the model
- `vision.py`: `VisionClient` calls Featherless `/chat/completions` (OpenAI-compatible, via `httpx`) with one image per request, temperature 0.1, one retry. The prompt asks for one JSON object (ego action, other vehicles, signals, collision). `parse_frame_analysis` strips code fences/prose, validates with lenient Pydantic models (clamps confidence, maps amber→yellow, unknown actions→`moving`)
- `__init__.py`: frames are analysed with 4 worker threads; per-frame findings collapse into state changes (signal colour per approach, action per vehicle, first visible collision). Each becomes an `Observation` with `t_rel_s = frame/fps + VIDEO_T0_OFFSET_S`, `locator = frame:<n>`, `time_uncertainty_s` = half the sampling interval, timestamp from the scenario reference time (17:42:00 IST). The dashcam vehicle gets `VIDEO_EGO_VEHICLE_ID` (default `V1`); other vehicles keep `vehicle_id: null` with `attributes.label`/`direction` for module 3 to resolve
- Model is `FEATHERLESS_VISION_MODEL` (default `google/gemma-3-27b-it`)
- No `FEATHERLESS_API_KEY`: the video is still validated and sampled, then `NotImplementedError` → pipeline serves the fixture. An invalid video raises `VideoError` (stage shows `error`). If every frame's model call fails it raises `RuntimeError`; partial failures become warnings
- `tests/test_ingestion.py` (11 tests): synthetic MJPG video in `tmp_path`; probe, rejection of non-video, sampling indices/times, the no-key fallback, a mocked model run that validates as `ObservationSet` and checks change-collapsing, `frame:<n>` and `t_rel_s`, the HTTP request shape via `httpx.MockTransport`, and lenient parsing
- No new dependencies (OpenCV, httpx, pydantic were already in requirements)
- Verification: `pytest` **23 passed**. Not run against the live Featherless API (no key in this session) and no real dashcam clip exists yet. Frontend untouched, so no frontend build was needed

## 2026-10-30 · Module 2: evidence extraction (sensor + witness)

Branch `claude/mod2-extraction-0b6631`. Implements both stubs in `src/backend/app/modules/extraction/`; signatures unchanged, so the pipeline picks them up without edits.

| File | Change |
| --- | --- |
| `modules/extraction/sensor.py` (new) | `extract_sensor` with Pandas. Detects the layout from headers (alias tables for time/speed/position/heading/brake/unit columns; signal logs by approach + state). Parses ISO and epoch-ms/s timestamps, computes `t_rel_s` against the reference time, converts mph and m/s to km/h and keeps `speed_<unit>_raw`, maps `OBD-7731` → V2. Emits key observations only: motion summary (first sample + `attributes.summary`), braking onset (brake flag rising edge, or ≥2 m/s² deceleration if there is no flag), entering the intersection box (±3.5 m), sudden heading change (≥10°), stopped, every signal change, and `sensor_gap` (step > 1.5× median interval) plus a warning. Locators `row:<n>` / `rows:<a>-<b>`, 1-based incl. header |
| `modules/extraction/witness.py` (new) | `extract_witness` with Featherless (OpenAI-compatible `/chat/completions` over `httpx`, no new dependency). Sends the statement with line numbers, asks for JSON. Every claim is checked: `raw_text` must be a verbatim quote (else dropped with a warning) and the `line:<n>` locator comes from where the quote really is. Vehicle ids by rule (red + sedan/car → V1, white + SUV/vehicle → V2, else null). Confidence = hedge-word lexicon blended with the LLM's certainty label. Clock times → `timestamp` + `time_uncertainty_s` (default ±300 s), `t_rel_s` null as in the fixture. `attributes.order` renumbered 1..n. No API key → `NotImplementedError` (fixture served) |
| `modules/extraction/__init__.py` | Re-exports both functions |
| `app/config.py` | Added `INCIDENT_REFERENCE_TIME` (env override, default `2026-10-12T17:42:00+05:30`) and `FEATHERLESS_WITNESS_MODEL` (env override, default `Qwen/Qwen2.5-7B-Instruct`). Additive only |
| `tests/test_extraction.py` (new) | Sensor extractor on the real `data/sensor` files checked against `scripts/scenario.py`: km/h values, mph conversion, V1 gap 6.0–7.5 s (2 samples, `rows:14-15`), braking onsets, signal changes = `SIGNAL_PHASES`, unique ids, unknown headers rejected, speed-only CSV. Witness extractor with a mocked LLM: validates, drops a hallucinated quote, corrects a wrong line number, maps vehicles, hedged claims score lower, orders renumbered; no-key fallback |

### Verification

- `pytest`: **28 passed**
- Uploading the 3 sensor CSVs and 2 witness files via the API and running analysis: the 3 sensor stages report `real` (28 observations total), witness stages report `fixture` without a key
- Not run: a live Featherless call (no key in this environment); frontend untouched, so `npm run build` not re-run

## 2026-10-30 · Module 3: timeline built

Branch `claude/mod3-timeline-7dcf2c`. `build_timeline` is now real (Pandas/NumPy rules, no LLM), so the pipeline reports stage 3 as `real`.

- `src/backend/app/modules/timeline/__init__.py`
  - **Time:** `t_rel_s`, else `timestamp` minus the reference time (uses `config.INCIDENT_REFERENCE_TIME` if present, else 2026-10-12 17:42:00 IST). Timestamps with uncertainty > 30 s (witness clock claims) are treated as "what happened", not "when". They join the nearest matching event without moving it or adding to its confidence.
  - **Order-only witness lines** are walked in `attributes.order` and matched monotonically to anchored events of the same kind. Lines with no match (W1 "SUV didn't brake", W2 "sped up") span the gap between their placed neighbours, with uncertainty = half the gap.
  - **Correlation:**
    - Same action family + compatible vehicle within 1 s + both uncertainties.
    - Signals group by phase: same approach and state until the approach changes, ending at the next change.
    - Collision and post-collision are incident-wide. A sudden heading change at collision time counts as impact evidence.
    - "Moving" ends at the vehicle's next event.
  - **Resolution pre-pass** for real module-1 output: video vehicles with no id are matched to known vehicles by direction and colour/type words, collision `vehicles` labels are remapped, and free-text signal approaches ("ego lane", "from the west") are normalised. It works on copies; inputs are never mutated.
  - **Confidence:** noisy-OR of members. Uncertainty is the best member's, widened by the spread.
- `tests/test_timeline.py` (new, 9 tests)
  - Sorted, valid, every observation referenced exactly once.
  - The collision at t=10 groups video, sensor and witness ids, including both wall-clock claims.
  - Signal phases: NB yellow 7→10, NB red 10, EB red −20→11, EB green 11.
  - V1 braking ~9.0, V2 braking 9.5–10, V2 enters against red at 9.1.
  - `t_rel_s` is derived from `timestamp`.
  - Real-video input shapes are resolved.
- `tests/test_api.py`: the demo-run test required *every* stage to be `fixture`, which breaks as soon as any module lands. It now requires the three extractor stages to be `fixture` (no uploads) and no stage to be `error`.
- Differences from `fixtures/timeline.json`:
  - V1 entering at t=10 and the heading changes are kept apart from or folded into the collision differently.
  - NB green (vid_001 + W1) is its own signal event.
  - The witness-only claims get their own events.
- Verification: `pytest` 21 passed. Frontend untouched.

## 2026-10-30 · Module 4: contradiction detection

`detect_conflicts(observation_sets, timeline)` is implemented, so stage 4 runs as **real**.

- `src/backend/app/modules/contradictions/context.py`: indexes over observations and timeline. Witness claims get their time from the timeline event that lists them, or (for "its light was green", "sped up") from the vehicle's sensor-anchored junction entry. Approach comes from signal claims, heading or direction; a dashcam "ego lane" maps to the ego vehicle's approach
- `rules.py`: seven rules
  - numeric: non-sensor `speed_kmh` vs sensor speed of the same vehicle in the claim's window, 15 km/h tolerance, "at least"/"at most" qualifiers as bounds; ≥20 km/h off is high
  - timestamp: witness clock time ± uncertainty vs the sensor timestamp of the same timeline event; consistent witnesses are cited too
  - semantic: signal claims vs the controller phase (same approach, same time); "accelerating"/"sped up" vs the measured speed trend; hedged or conflicting vehicle body types
  - missing evidence: `sensor_gap` overlapping a signal change, event onset or the collision; pre-collision events backed by one source type
- `llm.py`: optional Featherless pass (`FEATHERLESS_API_KEY`, model from `FEATHERLESS_CONTRADICTIONS_MODEL`) over witness quotes no rule flagged. Unknown ids are dropped; no key or any error skips it
- `tests/test_contradictions.py`: 12 tests. Every `KNOWN_DISCREPANCIES` entry and the V1 gap are found with the right type, values checked against `scenario.py`, all ids exist, LLM filtering and fallback, "ego lane" mapping
- `tests/test_api.py`: the demo-run test now accepts `real` as well as `fixture` stages (it failed as soon as any module was built)
- On the fixtures the report has 8 conflicts: the 7 in `fixtures/conflicts.json` plus "V2 begins braking (late)" having a single source
- `pytest`: 24 passed. `npm run build`: OK

## 2026-10-30 · Module 5: trajectory reconstruction (branch `claude/mod5-reconstruction-a604bb`)

**Backend** (`src/backend/app/modules/reconstruction/`: `__init__.py`, new `builder.py`)
- `build_reconstruction` is now real. The pipeline reports stage 5 as `real`
- Keyframes: any observation with a time and numeric `x_m`/`y_m` (the most confident one per vehicle and instant). They are resampled to a 0.5 s grid on the predefined intersection (extent 60 m, 3.5 m lanes, Main Rd / 5th Ave)
- Between keyframes whose speed and heading agree with the displacement, the vehicle holds speed and then brakes or accelerates uniformly to the next measured speed. This is exact for the scenario and bridges V1's 6.0–7.5 s gap at constant velocity. If that profile can't fit, it falls back to cubic Hermite, then to a straight line (e.g. across the collision impulse). Outside the keyframes a vehicle continues at constant velocity, or holds if stopped
- Vehicles with no positions get an approach-direction default (from `direction`/`approach`/`heading_deg`): constant speed in their left-hand-traffic lane, reaching the conflict point at collision time
- Collision marker only if a timeline `collision` event references known observations. It is placed at the vehicles' closest approach within the event's time ± uncertainty
- Window: collision time −5 s to +3 s (5–13 s on the demo), else derived from the last keyframe
- `source_observation_ids` lists only the measured keyframes, so the viewer can tell measured points from interpolated ones (no contract change)
- On the fixtures, V1/V2 tracks match `scenario.py` to within 1 cm up to t=10 s. The marker sits at (-1.75, 1.75), t=10.0

**Frontend** (`src/frontend/src/components/ReconstructionViewer.jsx`, same props)
- Fetches `/api/observations` itself to find measured keyframe times and signal phases
- Past path is solid between measured points and dashed where interpolated, with dots on measured points; the faint full planned path is also shown
- Stop lines on the inbound lanes. Signal heads with the current state (sensor > video > witness, falling back to a lower source when the better one hasn't reported yet), plus a legend
- Auto / Junction / Full zoom (auto follows the vehicles). Labels are laid out so they don't overlap: vehicle labels sit right of the car, the impact label left of the marker, with leader lines. The collision marker is drawn under the vehicles
- Checked in headless Chrome against a live backend at t = 5, 8.5, 9.7, 10.0, 10.2, 12.5 s

**Tests**
- New `tests/test_reconstruction.py` (13 tests): window, monotonic 0.5 s tracks, V1/V2 within 1 m of (-1.75, 1.75) at t=10, match to ground truth through the sensor gap, collision marker present, no marker without a supported collision event, approach defaults without sensors, dense 0.5 s telematics with the gap, pipeline stage `real`
- `tests/test_api.py`: the "all stages fixture" assertion now checks only the extractor stages (same change as mod3's branch, so the merge on that line is trivial)
- `pytest`: 26 passed. `npm run build`: OK

## 2026-10-30 · Modules 1–5 merged into `feature/prototype`

- Merged `claude/mod1-video-ingestion-1c16c1`, `claude/mod2-extraction-0b6631`, `claude/mod3-timeline-7dcf2c`, `claude/mod4-contradictions-4e1221` and `claude/mod5-reconstruction-a604bb`, in that order
- Conflicts: `docs/BUILD_LOG.md` (every branch appended an entry; all entries kept, in merge order) and `tests/test_api.py` (modules 3 and 4 relaxed the same demo-run assertion; kept module 3's version)
- `tests/test_api.py`: the demo run now requires stages 3–5 to be `real`. New `test_real_sensor_evidence_flows_through_modules_2_to_5` uploads the three `data/sensor` CSVs and checks that real sensor extraction feeds real timeline, contradictions and reconstruction: no stage errors, both vehicles enter the junction at t≈10, the V1 6.0–7.5 s gap is reported, and both tracks are within 1 m of (-1.75, 1.75) at t=10
- Verification: `pytest` **75 passed**, frontend build OK. Dashboard demo run in the browser: stages 1–2 `fixture` (no Featherless key), 3–5 `real`, 16 timeline events, 12 conflicts, collision marker at t=10 s (-1.75, 1.75), no console errors

### Integration issues found (not fixed yet)

1. **The W1 "green light" conflict is lost end to end.** Module 3 groups `wit_w1_02` (W1: V1's light was green on approach) into the t=0 "Northbound signal green" event, where it agrees with the signal. Module 4 finds conflicts through timeline grouping, so it never compares the claim with the 7.0–10.0 s yellow phase. On `fixtures/timeline.json` module 4 finds it; on module 3's timeline it doesn't (8 → 12 conflicts, but this high-severity one is missing)
2. **Noisy single-source conflicts.** Module 4 flags every event backed by one source type as `missing_evidence` (e.g. "'V1 sensor data gap' has a single source", "'Eastbound signal red' has a single source"). With sensor-only uploads that's 15 of 17 conflicts
3. With sensor-only evidence no collision is inferred: there is no `collision` observation, although both vehicles show simultaneous sudden heading changes at the same point at t=10.5. Arguably correct ("only if supported by evidence"), but it could be inferred

## 2026-10-30 · Fix: module 4 on module 3's real timeline

Fixes integration issues 1 and 2 found after the merge. Only module 4 changed; module 3's grouping is left as is.

| File | Change | Why |
| --- | --- | --- |
| `src/backend/app/modules/contradictions/rules.py` · `signal_states` | A witness signal claim about a vehicle ("its light was green when it was approaching") is always checked against the controller phase just before that vehicle's sensor/video-anchored junction entry. Before, this only happened when the timeline hadn't placed the claim | Module 3 groups signal claims by their stated colour, so W1's "green" landed in the t=0 green event and agreed with itself. The high-severity W1 green-light conflict was lost |
| same · `speed_trends` | Same principle for "sped up to beat the light": witness claims use the run-up window (onset of the entry phase → entry) regardless of timeline placement | On module 3's timeline the window stretched to t=11.5 s, past the collision |
| same · `single_source_events` | Only flags the collision event and signal phases a vehicle entered on (probed 0.05 s before entry, as in `signal_states`). Other single-source events (sensor gaps, witness-only claims, motion, post-collision phases) are no longer conflicts | Every single-source event was flagged: 15 of 17 conflicts with sensor-only evidence |
| `tests/test_contradictions.py` | The `report` fixture is parametrized over the hand-written `fixtures/timeline.json` **and** module 3's `build_timeline` output, so every module-4 check also runs on the real pipeline input. New `test_single_source_flags_only_decisive_events`. The LLM-fallback test now compares with the rules-only result (7) instead of a hard-coded 8 | Module 4's tests only ever saw the fixture timeline, which hid issue 1 |
| `tests/test_api.py` | The sensor-only integration test asserts exactly two single-source conflicts: EB red (V2's entry) and NB yellow (V1's entry) | Guards issue 2 end to end |

Result: the demo run now gives exactly the 7 conflicts of `fixtures/conflicts.json` (by type and subject). Sensor-only uploads give 4 instead of 17. `pytest` **84 passed**. Dashboard rechecked: Contradiction Explorer (7), with the NB signal dispute back.

Still open: issue 3 (no collision inferred from sensor-only evidence) was left as is, by choice.

## 2026-10-30 · Fix: sensor extractor rejected video-derived CSVs

**Error:** with the uploaded example dataset (`original_multiview.mp4`, `frame_index.csv`, `visual_motion_proxy.csv`) both CSV stages ended in `error`: `ValueError: unrecognised sensor CSV headers`. The extractor knew only vehicle tracks (time + speed) and signal logs. These files are derived from the video, use relative `timestamp_s` seconds, and have neither layout. On error the pipeline also served the demo sensor fixture in their place.

| File | Change |
| --- | --- |
| `src/backend/app/modules/extraction/sensor.py` | `timestamp_s` / `time_s` / `t_s` / `t_rel_s` / `seconds` are now time columns of kind `rel` (seconds from the reference time, i.e. recording start = t=0, same convention as module 1; a warning says the offset is unverified) |
| same | **Optical-flow layout** (`<view>_<mean\|p95>_optical_flow...` columns): for each camera view, a p95 flow with robust z ≥ 2.5 (median/MAD) and ≥ 1.5× the view median becomes a `motion_spike` observation (`vehicle_id` null, confidence 0.3 + 0.1·z capped at 0.8, ±half the sample step). Numbered in time order. A blank first row is expected (flow needs two frames); later blanks are reported as gaps |
| same | **Frame-index layout** (time + frame column only): no observations, plus a warning describing the range. It's a lookup table, not evidence |
| same | Unknown layouts still raise, now with a hint listing the accepted columns |
| `tests/test_extraction.py` | 3 tests: frame index → no observations and no error; synthetic flow CSV → spikes in time order, correct locator, gap warning; unknown layout → error with hint |

Result on the example dataset: both CSV stages `real` (frame index: 0 observations; motion proxy: spikes at t=24 s in view 3, z=2.8, and t=45 s in view 1, z=2.7). Timeline, contradictions and reconstruction ran without error. `pytest` **87 passed**. No console errors on the dashboard.

**Still open:** the video stage has no `FEATHERLESS_API_KEY`, so it serves `fixtures/video_observations.json`, the made-up Gachibowli V1/V2 collision. The timeline for this real dataset therefore mixes demo events (t=0–11.5 s) with the real motion spikes, and the reconstruction shows the demo collision.

## 2026-10-30 · Fix: demo fixtures no longer mixed into runs on real evidence

With real uploads, any stage that couldn't run (no Featherless key, unbuilt, or failed) used to serve its demo fixture. The example dataset's timeline therefore showed the made-up V1/V2 collision next to its real motion spikes.

| File | Change |
| --- | --- |
| `src/backend/app/pipeline.py` | Two modes. **Demo** (no evidence uploaded): unchanged, stages fall back to fixtures. **Live** (evidence uploaded): a stage raising `NotImplementedError` is `skipped`, one that fails is `error`, and neither contributes data. An extractor adds no observation set; stages 3–5 get an empty `Timeline` / `ConflictReport` / `Reconstruction` (`_empty`). Stage messages say "served demo fixture" or "no output" |
| `src/backend/app/schemas.py` | `StageResult.status` gains `"skipped"`. Additive contract change, so `SCHEMA_VERSION` 1.0.0 → 1.0.1. JSON schemas re-exported (`docs/contracts/schemas/`) |
| `src/frontend/src/components/EvidenceManager.jsx` | Amber badge for `skipped`. With uploads, the hint reads "Results use only your evidence; stages that can't run are skipped." |
| `tests/test_api.py` | The upload test now asserts live mode: witness `skipped` (no key), junk CSV `error`, no `fixture` status, and empty observations/timeline/conflicts/reconstruction |

Checked: modules 3–5 accept empty input (0 events, 0 conflicts, 0 vehicles). `pytest` **87 passed**, frontend build OK. Example dataset rerun in the dashboard: video `skipped`, both CSVs `real`, timeline has only the two motion spikes (t=24 s, t=45 s), 0 conflicts, no vehicles or collision, no console errors.
