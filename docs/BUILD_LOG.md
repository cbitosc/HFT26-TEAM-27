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
