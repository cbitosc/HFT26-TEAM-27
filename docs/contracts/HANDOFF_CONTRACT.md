# Hand-off contract

Who owns what, the exact function each module must implement, and what "done" means.
Each module is a stub that raises `NotImplementedError`. Until it stops raising, the
pipeline ([`pipeline.py`](../../src/backend/app/pipeline.py)) serves that module's fixture
and the dashboard shows the stage as **fixture**. When your function returns a valid model,
the stage turns **real** and nothing else has to change.

| # | Module | File you own | Function (do not change the signature) | Replaces fixture | Done when |
| --- | --- | --- | --- | --- | --- |
| 1 | Evidence ingestion | `src/backend/app/modules/ingestion/` | `analyze_video(evidence: Evidence) -> ObservationSet` | `video_observations.json` | Given a video, returns observations with timestamps and `frame:<n>` source refs; fixed cameras also get the road's direction (`vanishing_point_px`) from its lane lines, and one linked track per AI-sighted vehicle when motion tracking finds few |
| 2a | Witness extraction | `src/backend/app/modules/extraction/` | `extract_witness(evidence) -> ObservationSet` | `witness_observations.json` | `data/witness/*.txt` gives valid observations: vehicles, actions, `attributes.order` (+ `order_source`: stated / text position), approximate times with uncertainty, confidence that reflects hedging, explicit `hedges` / `vehicle_type_alternatives` / `colour_alternatives` / `ambiguity`; instruction-like lines in a statement are removed before the model sees them. Audit: `scripts/audit_witness.py` |
| 2b | Sensor normalisation | `src/backend/app/modules/extraction/` | `extract_sensor(evidence) -> ObservationSet` | `sensor_observations.json` | All three `data/sensor/*.csv` formats parse (and any layout recognised from header + values), timestamps are parsed, speed units (km/h, mph, m/s, knots, ft/s; from the header) are normalised to km/h with the raw value kept, movement events + the full path are extracted, gaps appear as `sensor_gap` observations and warnings, bad rows are skipped with a warning. Audit: `scripts/audit_sensor.py` |
| 2c | Fusion | `src/backend/app/modules/fusion/` | `fuse(observation_sets) -> ObservationSet \| None` | — | Infers events that need two files side by side (collision from two vehicles' simultaneous heading changes); derived observations cite their sources in `attributes.inferred_from` |
| 3 | Timeline | `src/backend/app/modules/timeline/` | `build_timeline(observation_sets) -> Timeline` | `timeline.json` | Sorted events that group correlated observations and keep their `observation_ids` |
| 4 | Contradictions | `src/backend/app/modules/contradictions/` | `detect_conflicts(observation_sets, timeline) -> ConflictReport` | `conflicts.json` | Finds the discrepancies listed in `scripts/generate_witness_statements.py::KNOWN_DISCREPANCIES` and the V1 sensor gap |
| 5 | Reconstruction (backend) | `src/backend/app/modules/reconstruction/` | `build_reconstruction(obs, timeline, conflicts) -> Reconstruction`; `build_event_analysis(obs, timeline) -> Reconstruction \| None` | `reconstruction.json` | Top-down tracks in metres for every positioned vehicle, a collision marker only if evidence supports it, a labelled schematic plan (no metres, speeds or impact) for an uncalibrated fixed camera, and a `note` when nothing can be reconstructed; event analysis = footage + image-space tracks |
| 5 | Reconstruction (frontend) | `ReconstructionViewer.jsx` (bottom, top-down map) and `EventAnalysis.jsx` (top right, footage) | render the two outputs | — | The user can replay movements with the time slider |
| 6 | Dashboard | `src/frontend/src/` (everything else) | consumes `/api/*` | — | The user can upload evidence, run analysis and see all results in one place |

## Rules for each hand-off

- **Inputs:** `Evidence.stored_path` is the uploaded file on disk. Treat it as read-only.
- **Outputs:** return the Pydantic model. Don't write files yourself; the pipeline persists outputs to `storage/runs/latest/`.
- **Failure:** raise a normal exception. The pipeline records `status: "error"`, serves your fixture, and the rest of the run continues.
- **LLM calls (Featherless):** read `FEATHERLESS_API_KEY` / `FEATHERLESS_BASE_URL` from `app.config`. Never hard-code keys. If no key is set, still raise `NotImplementedError` or fall back to rules, so the demo keeps working offline.
- **Tests:** add `tests/test_<module>.py`. Run your module against the files in `data/` and assert the output validates and agrees with `scripts/scenario.py`.
- **Downstream modules (3–5)** must work on the *fixture* observation sets as well as on real ones. That is how 3, 4 and 5 can be built in parallel with 1 and 2.
- Stay inside your folder. Changing `schemas.py` follows [INTEGRATION_CONTRACT.md § Changing the contract](INTEGRATION_CONTRACT.md#changing-the-contract).
