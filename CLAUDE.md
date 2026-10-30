# CLAUDE.md

Traffic-incident reconstruction for CBIT Hacktoberfest '26 (TEAM-27, NeuralNarcissists).
The app takes dashcam video, witness statements and sensor CSVs, then builds one
timeline, a list of contradictions between sources, and an animated reconstruction.

## Layout

```
src/backend/app/
  schemas.py          integration contract (Pydantic). Read this first.
  pipeline.py         runs modules 1→5 (+ 2c fusion); demo mode serves fixtures, live mode skips
  jobs.py             background runs with live progress (one at a time)
  report.py           investigator report (Markdown/JSON) with citations
  store.py            evidence index, run history (storage/runs/<id>), audit log (storage/audit.jsonl)
  routers/            FastAPI endpoints (/api/evidence, /api/analysis/run, /api/timeline, ...)
  modules/<name>/     one package per architecture module; each one is a stub until built
src/frontend/src/     React + Vite + Tailwind dashboard: Evidence Manager, Event Analysis (footage), Timeline,
                      Contradictions, Reconstruction (top-down map, bottom)
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
cd src/backend && ../../.venv/bin/uvicorn app.main:app --reload --port 8000 --env-file ../../.env   # .env holds FEATHERLESS_API_KEY (git-ignored)
npm --prefix src/frontend install && npm --prefix src/frontend run dev   # http://localhost:5173, proxies /api to :8000
.venv/bin/python scripts/generate_sensor_data.py   # regenerate data/ from scenario.py
.venv/bin/python scripts/generate_witness_statements.py
.venv/bin/python scripts/export_schemas.py         # after any schemas.py change
.venv/bin/python scripts/analyze.py data/witness/*.txt data/sensor/*.csv --case data/case.json   # CLI -> reports/<run>/report.md
cd scripts && ../.venv/bin/python make_cases.py && cd ..    # build eval/cases (synthetic, with answer keys)
.venv/bin/python scripts/evaluate.py [--no-llm]             # score every case; must stay all-pass
```

## Rules

- **The contract is `schemas.py`.** Don't change a field without following docs/contracts/INTEGRATION_CONTRACT.md. A contract change updates schemas, fixtures, exported schemas and frontend together.
- **Stay in your module.** Each parallel agent owns one folder (see docs/contracts/HANDOFF_CONTRACT.md). Implement the existing stub function and keep its signature. Don't edit `pipeline.py` to wire your module in; it already calls it.
- **Fixtures are frozen examples.** Don't edit them to make your module's tests pass. If the contract really changes, update them on purpose.
- **No scenario knowledge in code.** Vehicle names, junctions, traffic side, lane-marking standards and clocks are case settings (`PUT /api/incident`, see INTEGRATION_CONTRACT). The demo declares its own in `data/case.json`; tests that use demo data load it with the `demo_case` fixture.
- **Judge changes on all evaluation cases, not one clip.** `scripts/make_cases.py` builds `eval/cases/*` (diverse synthetic cases with answer keys); `scripts/evaluate.py` scores them. A change that helps one case and breaks another is a regression. Add a case when you add a capability.
- **Never guess what can't be known.** No marking standard → no calibration; no reference time → no clock times; an AI model's reading is labelled `inferred`, never presented as measured.
- Time is `t_rel_s` (seconds from reference). Space is metres with +x east, +y north. Speed is km/h.
- Featherless keys come from env (`FEATHERLESS_API_KEY`) via `app.config` and never get committed. With no key, modules must degrade gracefully.
- Log every change: add an entry to `docs/BUILD_LOG.md` listing what changed, which files, and why.
- `pytest` must pass and `npm --prefix src/frontend run build` must succeed before you hand off.
