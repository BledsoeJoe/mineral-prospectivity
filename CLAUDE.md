# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demonstration project (job application to KoBold Metals) for mineral
prospectivity mapping: a staged ingest pipeline over open USGS data, a
positive-unlabeled LightGBM model with spatial cross-validation, a FastAPI
artifact server, and a React + deck.gl frontend. The project's identity is
*honest evaluation*: much of the code exists to prevent three failure modes
(PU labels treated as negatives, spatial CV leakage, exploration-effort bias),
and the README/frontend prose documents those decisions deliberately. Keep
that voice: docstrings explain *why*, and judgment calls are written down.

## Style
Please refrain from the use of any em dashes and use US english word choices.

## Commands

Docker compose is the canonical environment (the local `.venv` may lack dev
deps; `lightgbm` needs the `libgomp1` system package).

```bash
docker compose up --build                    # postgres + pipeline(synth) + dagster + api + web (127.0.0.1:8080; dagster UI at /dagster/)
MPM_PROFILE=us-cu docker compose up --build  # real-data US copper profile

# tests + lint (run inside the image; postgres-backed tests skip without DATABASE_URL)
docker compose run --rm pipeline sh -c "pytest -q && ruff check src tests"
docker compose run --rm pipeline pytest tests/test_etl.py::test_stage_skips_when_fresh  # single test

# pipeline CLI (same code Dagster orchestrates)
docker compose run --rm pipeline python -m mpm etl run --profile us-cu --data-root /data
docker compose run --rm pipeline python -m mpm etl status --data-root /data
docker compose run --rm pipeline python -m mpm etl run --profile us-cu --force cube   # surgical rebuild
docker compose run --rm pipeline python -m mpm etl backfill-db --data-root /data      # rebuild catalog from artifacts

# dagster materialization of one profile partition
docker compose run --rm dagster bash -c 'mkdir -p $DAGSTER_HOME && cp docker/dagster.yaml $DAGSTER_HOME/dagster.yaml && dagster asset materialize --select "labels,geophysics,geochemistry,extra_covariates,cube,prospectivity_run" --partition us-cu -f src/mpm/orchestration/defs.py'

# frontend dev (proxies /api to :8000)
cd web && npm install && npm run dev

# postgres inspection (catalog tables are schema-qualified!)
docker compose exec postgres psql -U mpm mpm -c 'select run_id, profile from catalog.runs'
```

Compose builds a **separate image tag per service** from the same Dockerfile:
after changing Python code, rebuild `pipeline`, `api`, AND `dagster`
(`docker compose build pipeline api dagster`). After recreating the `api`
container, restart `web` too; nginx resolves the upstream IP at startup.

## Architecture

### Two orchestration layers that compose, not compete

- `src/mpm/etl/flow.py` owns the pipeline **logic**: each stage is a public
  function (`stage_labels`, `stage_geophys`, `stage_geochem`, `stage_extras`,
  `stage_cube_usgs`, `stage_train_publish`) executed via
  `stages.run_stage()`, which skips work when the stage's `manifest.json`
  fingerprint (upstream file sha256s + config slice + `ETL_VERSION`) is
  unchanged and all outputs exist. **Bump `ETL_VERSION` after any breaking
  stage-logic change**; fingerprints only see inputs/config, not code.
- `src/mpm/orchestration/defs.py` wraps those same functions as Dagster
  assets (partitions = usgs profiles). Dagster decides *when* steps run; the
  manifests decide whether there is work. "Materialize all" is always cheap.
  **Do not add `from __future__ import annotations` to defs.py**; stringified
  annotations break Dagster's `context` parameter detection.

Dataflow (usgs profiles; `synth` replaces the left side with a generator):

```
acquire ──┬─ labels ────┐
          ├─ geophys ───┤
          ├─ geochem ───┼─ cube ── train+publish ──► data/runs/<profile>/<run_id>/
          └─ extras ────┘                            (+ catalog.runs / catalog.run_cells)
```

Manifests store output paths **relative to the stage directory** so the same
`./data` bind mount is fresh from host and container. Profiles (region,
commodity, label rules, config overrides) live in `etl/profiles.py`; adding
a commodity system is a new profile entry, not new pipeline code.

### The catalog (Postgres, optional everywhere)

`src/mpm/db.py`: source registry (including user-registered extras), run
index, per-cell scores. All tables live in the **`catalog` schema** because
Dagster's own storage owns `public.runs` in the same database; never create
unqualified tables. Every function degrades gracefully without
`DATABASE_URL` (file-mode fallback), which is why the test suite passes with
or without postgres. Artifacts on the volume are the source of truth; the
catalog is always rebuildable from them (`backfill-db`), never the reverse.

User-added sources (`POST /api/sources`, or the frontend Architecture page)
are host-allowlisted (`MPM_SOURCE_HOSTS`, default mrdata.usgs.gov) because
the pipeline fetches whatever is registered. Two kinds: `xyz_grid`
(lon/lat/value triples) and `ngdb_geochem` (NGDB-style csv zips). Curated
suggestions live in `sources.SUGGESTED_SOURCES`; only add entries there after
verifying the file downloads and parses with the current readers.
`POST /api/materialize {profile}` launches a single-partition Dagster run via
GraphQL (`orchestration/client.py`, `MPM_DAGSTER_URL`); the API container
never runs the pipeline itself (its volume is read-only).

### Publish contract with the frontend

`etl/publish.py` writes immutable run dirs: `report.json` (metrics + audits +
provenance), `cells.json.gz` (compact short-key per-cell payload the API
serves pre-gzipped), `curves.json`, `importance.json`, `scores.parquet`, and
a `latest.json` pointer. `mpm/api.py` is deliberately thin; if the frontend
needs new data, add it at publish time, not as API-side computation.

### The storage seam (`src/mpm/storage.py`)

Published runs are addressed through `Storage`, a thin shell over an fsspec
filesystem, so `publish.py` and `api.py` work against a local directory or an
S3 prefix unchanged. **Only the runs tree moves**: `MPM_RUNS_ROOT` (a path or
`s3://bucket/prefix`) overrides `<data_root>/runs`, while the raw download
cache and stage manifests stay on local disk, where rename atomicity and cheap
stat calls are what freshness is built on.

- **Reads stream.** `iter_chunks`/`open` are the default; `read_bytes` is for
  small JSON pointers only. Serving `cells.json.gz` with a whole-body read is
  a regression that passes every round-trip test, so `tests/test_api.py` pins
  it by making `read_bytes` raise.
- **Ordering is the transaction.** No multi-key atomicity, so publish writes
  the immutable run dir first and `latest.json` last.
- **S3 has no directories.** A run is identified by the `report.json` it
  contains, not by its folder; `mkdir` is local-only (but `exist_ok=False`
  still guards both backends).
- `stage_train_publish` records a local `published.json` marker as its output,
  not the run dir, because manifest freshness stats paths relative to the
  stage dir. A **remote** runs root is therefore a fingerprint input, so
  repointing at a bucket republishes; a local one is not, because absolute
  paths differ between a host checkout and the bind mount.
- Credentials come from botocore's chain, never from arguments.

### Frontend

`web/`: React + react-router; `pages/Dashboard.jsx` (deck.gl H3 map + SVG
charts, no chart library) plus prose pages (About/Research/Breakdown/
Architecture; Breakdown pulls live numbers from the latest report). Colors
come from CSS custom properties in `theme.css` (light + dark); sequential map
ramps are percentile-binned and **reversed in dark mode**. The "top N%" view
is an emphasis treatment (fade the tail), not per-hex outlines.

## Modeling invariants (violating these produces plausible-but-wrong maps)

- **Effort quarantine**: `DEFAULT_EFFORT_PATTERNS` in `config.py` is the
  arbiter of which covariates the headline (geology) model may see. Covariate
  *naming* is therefore semantic: `sample_density_*`, `dist_to_deposit*`,
  `effort*`, and `_isna` suffixes route to the effort/audit model. Missingness
  indicators are effort by definition (survey extent).
- **`dist_to_nearest_km` must stay leave-self-out** (`k=2` for positive
  cells); otherwise it is a copy of the label; a regression here once shipped
  PR-AUC 1.000. `tests/test_etl.py` pins this.
- Zero-vs-missing semantics: MRDS/sample-density counts fillna(0) (absence is
  information); measured covariates stay NaN for `clean_cube` imputation.
- Headline metrics are pooled out-of-fold; per-fold stats are spread
  estimates only. Labels for `us-cu` are MRDS Producer/Past-Producer with
  `prod_size ∈ {L, M}`; loosening this ~10×'s the positive count and changes
  what the label means; treat it as a documented judgment call.
- Real-data cleaning traps already handled in `etl/readers.py`: NGDB censored
  values encoded as negative detection limits (substitute DL/2), ppm/pct
  mixing, UTF-8 BOMs present in some NGDB media but not others, MRDS
  Null Island rows and free-text commodity fields.

## Tests

`tests/` targets silent-failure paths (stage freshness, label leaks,
quarantine routing, validation gates, API contract). New pipeline behavior
should come with a test of its failure mode, not just its happy path;
that pattern is load-bearing for the project's credibility.
