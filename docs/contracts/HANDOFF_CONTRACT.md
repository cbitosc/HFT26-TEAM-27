# Hand-off contract

Who owns what, the exact function each module must implement, and what "done" means.
Each module is a stub that raises `NotImplementedError`. Until it stops raising, the
pipeline ([`pipeline.py`](../../src/backend/app/pipeline.py)) serves that module's fixture
and the dashboard shows the stage as **fixture**. When your function returns a valid model,
the stage turns **real** and nothing else has to change.

| # | Module | File you own | Function (do not change the signature) | Replaces fixture | Done when |
| --- | --- | --- | --- | --- | --- |
| 1 | Evidence ingestion | `src/backend/app/modules/ingestion/` | `analyze_video(evidence: Evidence) -> ObservationSet` | `video_observations.json` | Given a video, returns observations with timestamps and `frame:<n>` source refs |
| 2a | Witness extraction | `src/backend/app/modules/extraction/` | `extract_witness(evidence) -> ObservationSet` | `witness_observations.json` | `data/witness/*.txt` gives valid observations: vehicles, actions, `attributes.order`, approximate times, confidence that reflects hedging |
| 2b | Sensor normalisation | `src/backend/app/modules/extraction/` | `extract_sensor(evidence) -> ObservationSet` | `sensor_observations.json` | All three `data/sensor/*.csv` formats parse, speeds are in km/h, gaps appear as `sensor_gap` observations and warnings |
| 3 | Timeline | `src/backend/app/modules/timeline/` | `build_timeline(observation_sets) -> Timeline` | `timeline.json` | Sorted events that group correlated observations and keep their `observation_ids` |
| 4 | Contradictions | `src/backend/app/modules/contradictions/` | `detect_conflicts(observation_sets, timeline) -> ConflictReport` | `conflicts.json` | Finds the discrepancies listed in `scripts/generate_witness_statements.py::KNOWN_DISCREPANCIES` and the V1 sensor gap |
| 5 | Reconstruction (backend) | `src/backend/app/modules/reconstruction/` | `build_reconstruction(obs, timeline, conflicts) -> Reconstruction` | `reconstruction.json` | Tracks for every vehicle over `[t_min_s, t_max_s]`, and a collision marker only if evidence supports it |
| 5 | Reconstruction (frontend) | `src/frontend/src/components/ReconstructionViewer.jsx` | renders `Reconstruction` | — | The user can replay movements with the time slider |
| 6 | Dashboard | `src/frontend/src/` (everything else) | consumes `/api/*` | — | The user can upload evidence, run analysis and see all results in one place |

## Rules for each hand-off

- **Inputs:** `Evidence.stored_path` is the uploaded file on disk. Treat it as read-only.
- **Outputs:** return the Pydantic model. Don't write files yourself; the pipeline persists outputs to `storage/runs/latest/`.
- **Failure:** raise a normal exception. The pipeline records `status: "error"`, serves your fixture, and the rest of the run continues.
- **LLM calls (Featherless):** read `FEATHERLESS_API_KEY` / `FEATHERLESS_BASE_URL` from `app.config`. Never hard-code keys. If no key is set, still raise `NotImplementedError` or fall back to rules, so the demo keeps working offline.
- **Tests:** add `tests/test_<module>.py`. Run your module against the files in `data/` and assert the output validates and agrees with `scripts/scenario.py`.
- **Downstream modules (3–5)** must work on the *fixture* observation sets as well as on real ones. That is how 3, 4 and 5 can be built in parallel with 1 and 2.
- Stay inside your folder. Changing `schemas.py` follows [INTEGRATION_CONTRACT.md § Changing the contract](INTEGRATION_CONTRACT.md#changing-the-contract).
