# CLAUDE.md

Traffic-incident reconstruction for CBIT Hacktoberfest '26 (TEAM-27, NeuralNarcissists).
The app takes dashcam video, witness statements and sensor CSVs, then builds one
timeline, a list of contradictions between sources, and an animated reconstruction.

## Layout

```
src/backend/app/
  schemas.py          integration contract (Pydantic). Read this first.
  pipeline.py         runs modules 1→5; falls back to fixtures on NotImplementedError
  routers/            FastAPI endpoints (/api/evidence, /api/analysis/run, /api/timeline, ...)
  modules/<name>/     one package per architecture module; each one is a stub until built
src/frontend/src/     React + Vite + Tailwind dashboard (4 panels in components/)
fixtures/             hand-written example output of each module (the contract, as examples)
data/                 demo evidence: data/sensor/*.csv, data/witness/*.txt, data/video/
scripts/              scenario.py (ground truth), data generators, fixture author, schema export
docs/contracts/       INTEGRATION_CONTRACT.md, HANDOFF_CONTRACT.md, JSON schemas
docs/BUILD_LOG.md     log of every change made to the repo. Append to it.
tests/                pytest; fixtures must validate against schemas
```

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r src/backend/requirements.txt
.venv/bin/pytest                                   # from repo root
cd src/backend && ../../.venv/bin/uvicorn app.main:app --reload --port 8000
npm --prefix src/frontend install && npm --prefix src/frontend run dev   # http://localhost:5173, proxies /api to :8000
.venv/bin/python scripts/generate_sensor_data.py   # regenerate data/ from scenario.py
.venv/bin/python scripts/generate_witness_statements.py
.venv/bin/python scripts/export_schemas.py         # after any schemas.py change
```

## Rules

- **The contract is `schemas.py`.** Don't change a field without following docs/contracts/INTEGRATION_CONTRACT.md. A contract change updates schemas, fixtures, exported schemas and frontend together.
- **Stay in your module.** Each parallel agent owns one folder (see docs/contracts/HANDOFF_CONTRACT.md). Implement the existing stub function and keep its signature. Don't edit `pipeline.py` to wire your module in; it already calls it.
- **Fixtures are frozen examples.** Don't edit them to make your module's tests pass. If the contract really changes, update them on purpose.
- **Ground truth is `scripts/scenario.py`.** V1 red sedan northbound, V2 white SUV eastbound, collision at t=10.0 s at (-1.75, 1.75). Reference time is 2026-10-12 17:42:00 IST. Use it as the test oracle.
- Time is `t_rel_s` (seconds from reference). Space is metres with +x east, +y north. Speed is km/h.
- Featherless keys come from env (`FEATHERLESS_API_KEY`) via `app.config` and never get committed. With no key, modules must degrade gracefully.
- Log every change: add an entry to `docs/BUILD_LOG.md` listing what changed, which files, and why.
- `pytest` must pass and `npm --prefix src/frontend run build` must succeed before you hand off.
