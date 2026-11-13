# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A read-only OSINT analytics pipeline over the live `shaktidb_social` Postgres corpus:
extract → NLP/ontology → statistically gated metrics → alerts → dashboard.
Architecture: `current-arch.md`. Running history and open issues:
`collaboration/04_CURRENT_STATE.md`.

## Hard rules

1. **Never write to production Postgres.** Use `lib.pg.read_only_snapshot()`; only `SELECT`;
   always `ROLLBACK`. Do not add any code path that opens a writable connection to
   `shaktidb_social`.
2. **No secrets in code or docs.** Credentials come from `.env` via `lib.config`. Do not
   add fallback defaults for `SHAKTI_PG_*`, and do not write hosts or passwords into
   Markdown.
3. **Never commit corpus-derived data** — DuckDB files, parquet, `UI/data/*.json`, generated
   reports, or any CSV containing post text. `.gitignore` covers these; don't force-add.
4. **Metrics v2 (`data/metrics.duckdb`) is authoritative.** `05_metrics.py` / `06_validate.py`
   and `analytics.duckdb.metrics_daily` are legacy — never base conclusions on them.
5. **Hate scores are uncalibrated** (`hate_calibrated=false`). Do not surface hate
   prevalence as a finding. Reach is a proxy — label it so.
6. **Alert on `topic_id`**, not hashtags alone (see `docs/ALERTING_KEYING.md`).
7. DuckDB is single-writer — don't run two stages that write the same `.duckdb` at once.

## Environment

```bash
source .venv/bin/activate         # pip install -r requirements.txt
cp .env.example .env              # fill SHAKTI_PG_*
```

- `.venv` deliberately has **no torch/transformers**. GPU stages (`03`, `04`, `09`, `10`,
  `12`–`15`) need `PYTHONPATH` pointed at a torch-capable env; `15` and cuML paths need RAPIDS.
- If the GPU is held by vLLM, set `CUDA_VISIBLE_DEVICES=` to force CPU.
- Run scripts from the repo root; `lib.config` resolves paths from its own location.

## Common commands

```bash
python scripts/07_metrics_v2.py && python scripts/08_metrics_db.py   # metrics v2
python UI/build_dashboard.py                                         # rebuild snapshot
cd UI && ../.venv/bin/python3 api_server.py 8877                     # serve dashboard
bash scripts/run_rebuild.sh                  # 07→08→11→dashboard; FROM=08 resumes; 07 takes ~45-60 min, ~8 GB RAM
python -m pytest scripts/tests                                       # python tests
python -m pytest scripts/tests/test_fdr_family.py::test_name -x      # single test
python UI/tests/check_api_dimensions.py                              # UI API harnesses (use .venv python)
bash scripts/run_gemma_ner.sh                # Gemma routing (17) + entities (18) loop; touch logs/gemma_ner.stop to end
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d             # dashboard container (port 8877, see Dockerfile)
node UI/tests/check_taxonomy.mjs                                     # UI harnesses (see UI/tests/README.md)
```

## Architecture in brief

Data flows Postgres → `01` → foundation store (`data/parquet` + `analytics.duckdb`) → two parallel
branches: **metrics v2** (`07` → `data/metrics_stage/` → `08` → `data/metrics.duckdb`) and the
**geo-ontology layer** (`10`,`12`–`15`,`17`,`18` → `11` → `data/geo_ontology.duckdb`; Gemma NER lands
in `data/gemma_ner.duckdb`, fresh posts in `incoming_posts.duckdb`). `UI/build_dashboard.py` merges both into
`UI/data/metrics.json` + `geo_alerts.json` + `UI/index.html`; `UI/api_server.py` serves them plus read-only
`/api/*` queries (drill-down, live NER/Military view). `delivery/` copies the results into a Docker Postgres.
The `post_embeddings` view in `analytics.duckdb` points at an absolute parquet path — run
`scripts/repoint_embeddings_view.py` after moving the repo. Full diagram: `current-arch.md`.
UI tests are jsdom harnesses (`node_modules`) that render the real `index.html`, not unit tests.

## Conventions

- **Numbered scripts are pipeline stages** in run order; utilities are unnumbered.
- Every statistical primitive lives in `scripts/lib/stats_v2.py` — reuse it, don't
  reimplement NB baselines, BH-FDR, EB shrinkage or AR(1) inline. It runs self-checks; keep
  them passing.
- **Labels go through `scripts/lib/taxonomy.py`** — never hardcode category lists or display
  names in `dash.js` or the build script. Unknown categories must render with a warning,
  not be silently dropped (`UI/tests/check_taxonomy_drop.mjs` guards this).
- Platforms are derived from data (`platform_dim`), never hardcoded lists.
  `KNOWN_LOW_COVERAGE_PLATFORMS` is documentation only — don't filter on it.
- Below minimum support, emit **NULL, not 0**, and keep the support counts alongside.
- Experimental switches in `07` (`topic_source`, `count_mode`, `burst_method`, `chain_method`)
  stay OFF by default; adopt one only after `scripts/review/noise_gates.py` passes and a judged
  precision sample clears the floor (see `docs/TOPIC_KEYING_EVALUATION.md`).
- Write metric formulas into `metric_meta` / `docs/METRICS_V2_SPEC.md`; a formula change is
  a spec change.
- New stages that replace old outputs write new versioned tables (`_v2`, `_v3`) rather than
  overwriting, until the new version is verified.

## Verification expectations

This project has repeatedly found silent failures (schema drift producing zero rows,
exclusion via null timestamps, inverted null-controls, labels at <50% precision). When
changing anything:

- Check row counts and coverage before and after, per platform.
- For classifiers, hand-judge a fresh stratified sample; report precision and recall honestly.
- Record the verdict (SOUND / PARTIAL / UNSOUND) and caveats in the relevant spec and in
  `collaboration/04_CURRENT_STATE.md`.
- Run `scripts/audit_restatement.py` before adding a metric — if it just restates volume,
  it doesn't earn a column.
- After rebuilding metrics, rebuild the dashboard and run the UI harnesses.

## Where things are documented

| Topic | File |
|-------|------|
| Architecture | `current-arch.md`, `collaboration/03_ARCHITECTURE.md` |
| Metrics contract | `docs/METRICS_V2_SPEC.md` |
| Statistical review | `docs/STATISTICAL_REVIEW.md`, `docs/STATISTICAL_APPENDIX.md` |
| Ontology layer + verdicts | `docs/GEOPOLITICAL_ONTOLOGY_SPEC.md` |
| Metric critique | `docs/metrics-critique.md` |
| Locked decisions | `collaboration/02_DECISIONS.md` |
| How to run | `collaboration/05_HOW_TO_RUN.md` |
| Delivery DB | `delivery/README.md` |
