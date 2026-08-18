"""Dagster asset graph over the pipeline's stage functions.

The division of labour:

* :mod:`mpm.etl.flow` owns the *logic* -- each stage is one function, with
  its own content-addressed manifest cache. That layer is orchestrator-free
  and runs identically from the CLI, from tests, and from here.
* This module owns the *operations* -- lineage, partitioning, scheduling,
  retries, and the UI. Each asset is a thin wrapper that calls a stage
  function and reports its manifest as Dagster metadata.

The two caching layers compose rather than fight: Dagster decides *when* to
run a step; the stage manifest decides whether the step actually has work to
do. Materializing an asset whose inputs haven't changed is a fast no-op, so
"materialize all" is always safe -- the same property the CLI has.

Partitions are pipeline profiles (one cube per commodity system). The synth
profile stays outside Dagster on purpose: it is the offline test harness,
and its home is the CLI and the test suite.

Run it:

    dagster dev -f src/mpm/orchestration/defs.py     # UI on :3000
    # or in the compose stack: the `dagster` service

Storage note: with DAGSTER_HOME set and dagster.yaml pointing at Postgres
(docker/dagster.yaml), run history is shared between the webserver and any
`dagster asset materialize` invocations from other containers.
"""

from __future__ import annotations

import os
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetSelection,
    Definitions,
    MaterializeResult,
    MetadataValue,
    RunRequest,
    StaticPartitionsDefinition,
    asset,
    define_asset_job,
    schedule,
)

from mpm.etl import flow
from mpm.etl.acquire import acquire
from mpm.etl.profiles import PROFILES
from mpm.etl.sources import REGISTRY
from mpm.etl.stages import StageOutcome, load_outcome

USGS_PROFILES = sorted(p.name for p in PROFILES.values() if p.kind == "usgs")
profile_partitions = StaticPartitionsDefinition(USGS_PROFILES)


def _data_root() -> Path:
    return Path(os.environ.get("MPM_DATA_ROOT", "data"))


def _stages_root(profile_name: str) -> Path:
    return _data_root() / "stages" / profile_name


def _profile(context: AssetExecutionContext):
    return PROFILES[context.partition_key]


def _outcome_metadata(out: StageOutcome) -> dict:
    md = {
        "fresh_cache_hit": out.skipped,
        "duration_s": out.manifest.get("duration_s", 0.0),
        "outputs": MetadataValue.json(
            [{k: o.get(k) for k in ("name", "path", "rows", "bytes")} for o in out.outputs]
        ),
    }
    if out.manifest.get("meta"):
        md["stage_meta"] = MetadataValue.json(out.manifest["meta"])
    return md


# ------------------------------------------------------------------ raw layer
#
# Raw files are profile-independent (every cube reads the same national
# compilations), so these assets are unpartitioned. acquire() is cache-first:
# re-materializing verifies the checksum and returns.


def _raw_asset(source_name: str):
    src = REGISTRY[source_name]

    @asset(
        name=f"raw_{source_name}",
        group_name="raw",
        description=src.description,
        metadata={"url": src.url, "citation": src.citation, "license": src.license},
    )
    def _fetch() -> MaterializeResult:
        got = acquire(src, _data_root() / "raw")
        return MaterializeResult(
            metadata={
                "path": MetadataValue.path(str(got.path)),
                "bytes": got.bytes,
                "sha256": got.sha256,
                "from_cache": got.from_cache,
            }
        )

    return _fetch


raw_mrds = _raw_asset("mrds")
raw_magnetic = _raw_asset("magnetic")
raw_gravity_isostatic = _raw_asset("gravity_isostatic")
raw_gravity_bouguer = _raw_asset("gravity_bouguer")
raw_ngdb_sediment = _raw_asset("ngdb_sediment")


# ---------------------------------------------------------------- stage layer


@asset(
    partitions_def=profile_partitions,
    deps=[raw_mrds],
    group_name="cube",
    description="Deposit labels + exploration-effort covariates from MRDS.",
)
def labels(context: AssetExecutionContext) -> MaterializeResult:
    profile = _profile(context)
    acquired = flow.acquire_builtin(profile, _data_root() / "raw", refresh=False)
    out = flow.stage_labels(profile, _stages_root(profile.name), acquired, force=False)
    return MaterializeResult(metadata=_outcome_metadata(out))


@asset(
    partitions_def=profile_partitions,
    deps=[raw_magnetic, raw_gravity_isostatic, raw_gravity_bouguer],
    group_name="cube",
    description="Aeromagnetic + gravity grids aggregated to H3 cells.",
)
def geophysics(context: AssetExecutionContext) -> MaterializeResult:
    profile = _profile(context)
    acquired = flow.acquire_builtin(profile, _data_root() / "raw", refresh=False)
    out = flow.stage_geophys(profile, _stages_root(profile.name), acquired, force=False)
    return MaterializeResult(metadata=_outcome_metadata(out))


@asset(
    partitions_def=profile_partitions,
    deps=[raw_ngdb_sediment],
    group_name="cube",
    description="Stream-sediment geochemistry: censoring handled, per-cell medians.",
)
def geochemistry(context: AssetExecutionContext) -> MaterializeResult:
    profile = _profile(context)
    acquired = flow.acquire_builtin(profile, _data_root() / "raw", refresh=False)
    if "ngdb_sediment" not in acquired:
        return MaterializeResult(metadata={"skipped": "geochem disabled for this profile"})
    out = flow.stage_geochem(profile, _stages_root(profile.name), acquired, force=False)
    return MaterializeResult(metadata=_outcome_metadata(out))


@asset(
    partitions_def=profile_partitions,
    group_name="cube",
    description=(
        "User-added sources from the Postgres catalog (extra XYZ grids or "
        "NGDB-style geochem), gridded to H3. No-op when the catalog is empty."
    ),
)
def extra_covariates(context: AssetExecutionContext) -> MaterializeResult:
    profile = _profile(context)
    extras = flow.acquire_extras(_data_root() / "raw")
    out = flow.stage_extras(profile, _stages_root(profile.name), extras, force=False)
    if out is None:
        return MaterializeResult(metadata={"extra_sources": 0})
    md = _outcome_metadata(out)
    md["extra_sources"] = len(extras)
    return MaterializeResult(metadata=md)


@asset(
    partitions_def=profile_partitions,
    deps=[labels, geophysics, geochemistry, extra_covariates],
    group_name="cube",
    description="The joined model-ready datacube: one row per H3 cell.",
)
def cube(context: AssetExecutionContext) -> MaterializeResult:
    profile = _profile(context)
    root = _stages_root(profile.name)
    labels_out = load_outcome("labels", root / "labels")
    geophys_out = load_outcome("geophys", root / "geophys")
    geochem_out = None
    if (root / "geochem" / "manifest.json").exists():
        geochem_out = load_outcome("geochem", root / "geochem")
    extra_out = None
    if (root / "extras" / "manifest.json").exists():
        extra_out = load_outcome("extras", root / "extras")
    out = flow.stage_cube_usgs(
        profile, root, labels_out, geophys_out, geochem_out, extra_out, force=False
    )
    return MaterializeResult(metadata=_outcome_metadata(out))


@asset(
    partitions_def=profile_partitions,
    deps=[cube],
    group_name="modelling",
    description=(
        "Spatially cross-validated PU experiment; publishes web artifacts and "
        "registers the run in the catalog."
    ),
)
def prospectivity_run(context: AssetExecutionContext) -> MaterializeResult:
    profile = _profile(context)
    root = _stages_root(profile.name)
    cube_out = load_outcome("cube", root / "cube")

    acquired = flow.acquire_builtin(profile, _data_root() / "raw", refresh=False)
    extras = flow.acquire_extras(_data_root() / "raw")
    provenance = [a.provenance() for a in acquired.values()]
    provenance += [a.provenance() | {"kind": k} for a, k, _ in extras]

    out = flow.stage_train_publish(
        profile, root, _data_root() / "runs" / profile.name, cube_out, provenance, force=False
    )
    md = _outcome_metadata(out)
    meta = out.manifest.get("meta", {})
    if meta.get("run_id"):
        md["run_id"] = meta["run_id"]
    for k, v in (meta.get("headline") or {}).items():
        if v is not None:
            md[f"headline_{k}"] = float(v)
    return MaterializeResult(metadata=md)


# ------------------------------------------------------------------- schedule

# The job covers only the partitioned assets (they share one partitions def;
# mixing in the unpartitioned raw layer would be a job-definition error). The
# stage functions acquire raw files themselves, cache-first, so the job is
# self-sufficient; the raw assets exist for lineage and manual re-fetches.
build_all_job = define_asset_job(
    "build_prospectivity",
    selection=AssetSelection.groups("cube", "modelling"),
    partitions_def=profile_partitions,
)


@schedule(job=build_all_job, cron_schedule="0 6 * * 1", execution_timezone="UTC")
def weekly_refresh():
    """USGS revises these compilations in place; a weekly pass re-checks and
    rebuilds only what actually changed (stage manifests make it cheap).
    Off by default: turning on a scheduled fetch is an operator's decision."""
    for name in USGS_PROFILES:
        yield RunRequest(partition_key=name)

defs = Definitions(
    assets=[
        raw_mrds, raw_magnetic, raw_gravity_isostatic, raw_gravity_bouguer,
        raw_ngdb_sediment,
        labels, geophysics, geochemistry, extra_covariates, cube, prospectivity_run,
    ],
    jobs=[build_all_job],
    schedules=[weekly_refresh],
)
