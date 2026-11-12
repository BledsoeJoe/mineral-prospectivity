"""The pipeline itself: wire stages together for a profile.

Dataflow (usgs profiles)::

    acquire ──┬─ labels ────┐
              ├─ geophys ───┤
              ├─ geochem ───┼─ cube ── train+publish
              └─ extras ────┘

    synth profiles:  cube(synthetic) ── train+publish

Each box is a manifest-checked stage (see :mod:`mpm.etl.stages`): rerunning the
pipeline recomputes exactly the stages whose inputs or config changed, and
nothing else. ``--force`` exists for the day you don't trust that.

``extras`` are user-added sources from the Postgres catalog (see
:mod:`mpm.db`) -- additional XYZ grids or NGDB-style geochem zips that join
the cube without a code change. Without a database the stage is skipped.

The functions here are the single implementation of each stage; the CLI
(``mpm etl run``) drives them directly, and the Dagster assets in
:mod:`mpm.orchestration.defs` drive the same functions with lineage,
scheduling and a UI on top.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from mpm import db
from mpm.config import Config
from mpm.etl import gridding, readers, sources, validate
from mpm.etl.acquire import AcquiredFile, acquire
from mpm.etl.profiles import PROFILES, Profile
from mpm.etl.publish import publish_run
from mpm.etl.stages import StageContext, StageOutcome, run_stage
from mpm.ingest import attach_labels, clean_cube, points_to_cells, summarize
from mpm.pipeline import run_experiment
from mpm.report import build_report
from mpm.storage import Storage, runs_storage
from mpm.synth import make_synthetic_cube

log = logging.getLogger("mpm.etl")

ETL_VERSION = "4"
"""Bump to invalidate every stage manifest after a breaking logic change."""


def run_pipeline(
    profile_name: str,
    data_root: Path,
    force: tuple[str, ...] = (),
    refresh: bool = False,
) -> dict[str, object]:
    if profile_name not in PROFILES:
        raise SystemExit(f"unknown profile {profile_name!r}; available: {sorted(PROFILES)}")
    profile = PROFILES[profile_name]
    data_root = Path(data_root)
    stages_root = data_root / "stages" / profile.name
    # Raw downloads and manifests stay local; only published runs may be remote.
    runs_root = runs_storage(data_root) / profile.name
    force_all = "all" in force

    log.info("=== pipeline %s -> %s", profile.name, data_root)

    if profile.kind == "synth":
        cube_out = stage_cube_synth(profile, stages_root, force_all or "cube" in force)
        provenance = [{"source": "synthetic", "seed": profile.seed}]
    else:
        acquired = acquire_builtin(profile, data_root / "raw", refresh)
        extras = acquire_extras(data_root / "raw", refresh)
        provenance = [a.provenance() for a in acquired.values()]
        provenance += [a.provenance() | {"kind": k} for a, k, _ in extras]
        labels_out = stage_labels(profile, stages_root, acquired, force_all or "labels" in force)
        geophys_out = stage_geophys(profile, stages_root, acquired, force_all or "geophys" in force)
        # The profile decides the default; the catalog can veto (a disabled
        # source stays out of every future cube until re-enabled).
        include_geochem = (
            profile.include_geochem
            and "ngdb_sediment" in acquired
            and db.builtin_enabled("ngdb_sediment")
        )
        geochem_out = (
            stage_geochem(profile, stages_root, acquired, force_all or "geochem" in force)
            if include_geochem
            else None
        )
        extra_out = stage_extras(profile, stages_root, extras, force_all or "extras" in force)
        cube_out = stage_cube_usgs(
            profile, stages_root, labels_out, geophys_out, geochem_out, extra_out,
            force_all or "cube" in force,
        )

    publish_out = stage_train_publish(
        profile, stages_root, runs_root, cube_out, provenance,
        force_all or "train" in force or "publish" in force,
    )
    return {
        "profile": profile.name,
        "cube": str(cube_out.output_path("cube")),
        "run_id": publish_out.manifest["meta"].get("run_id"),
        "run_dir": publish_out.manifest["meta"].get("run_uri"),
        "skipped": {
            "cube": cube_out.skipped,
            "train_publish": publish_out.skipped,
        },
    }


# ------------------------------------------------------------------- acquire


def acquire_builtin(profile: Profile, raw_dir: Path, refresh: bool) -> dict[str, AcquiredFile]:
    names = ["mrds", "magnetic", "gravity_isostatic", "gravity_bouguer"]
    if profile.include_geochem and db.builtin_enabled("ngdb_sediment"):
        names.append("ngdb_sediment")
    return {n: acquire(sources.REGISTRY[n], raw_dir, refresh=refresh) for n in names}


def acquire_extras(
    raw_dir: Path, refresh: bool = False
) -> list[tuple[AcquiredFile, str, str]]:
    """Fetch every enabled user-added source from the catalog.

    Returns (file, kind, value_name) triples; empty without a database.
    """
    out = []
    for source, kind, value_name in db.extra_sources():
        out.append((acquire(source, raw_dir, refresh=refresh), kind, value_name))
    return out


# -------------------------------------------------------------------- stages


def stage_cube_synth(profile: Profile, root: Path, force: bool) -> StageOutcome:
    fingerprint = {
        "etl_version": ETL_VERSION,
        "kind": "synth",
        "resolution": profile.synth_resolution,
        "n_deposits": profile.synth_n_deposits,
        "seed": profile.seed,
    }

    def build(ctx: StageContext) -> None:
        df = make_synthetic_cube(
            resolution=profile.synth_resolution,
            n_true_deposits=profile.synth_n_deposits,
            seed=profile.seed,
        )
        path = ctx.dir / "cube.parquet"
        df.to_parquet(path, index=False)
        ctx.add_output("cube", path, rows=len(df))
        ctx.meta["summary"] = summarize(df)
        ctx.meta["hidden_truth"] = {
            "n_true_deposits": int(df["truth_deposit"].sum()),
            "n_discovered": int(df["deposit"].sum()),
        }

    return run_stage("cube", root / "cube", fingerprint, build, force)


def stage_labels(
    profile: Profile, root: Path, acquired: dict[str, AcquiredFile], force: bool
) -> StageOutcome:
    src = acquired["mrds"]
    fingerprint = {
        "etl_version": ETL_VERSION,
        "input_sha": src.sha256,
        "h3_res": profile.h3_res,
        "bbox": profile.bbox,
        "commodities": profile.commodities,
        "producer_only": profile.producer_only,
    }

    def build(ctx: StageContext) -> None:
        mrds = readers.read_mrds(src.path)
        mrds = gridding.bbox_filter(mrds, profile.bbox, "latitude", "longitude")
        validate.check_frame(
            mrds, "mrds",
            required_cols=("site_name", "latitude", "longitude", "commod1", "dev_stat"),
            min_rows=50_000, max_rows=2_000_000,
            bounds={"latitude": (-90, 90), "longitude": (-180, 180)},
        ).raise_if_failed()

        target = mrds[readers.mrds_commodity_mask(mrds, profile.commodities)]
        deposits = (
            target[target["dev_stat"].isin(readers.MRDS_DEPOSIT_STATUSES)]
            if profile.producer_only else target
        )
        if profile.prod_sizes:
            deposits = deposits[deposits["prod_size"].isin(profile.prod_sizes)]
        labeled = points_to_cells(
            deposits, resolution=profile.h3_res, lat_col="latitude", lon_col="longitude",
            name_col="site_name",
        )
        validate.check_labels(labeled, "labels", min_positives=30).raise_if_failed()

        # Everything MRDS knows about, any commodity, any status: a direct map
        # of historical exploration attention. Named so the quarantine list
        # catches it; it exists to feed the bias audit, never the headline model.
        effort = (
            mrds.assign(h3=gridding.assign_h3(mrds, profile.h3_res, "latitude", "longitude"))
            .groupby("h3").size().rename("effort_mrds_density").reset_index()
        )

        p1 = ctx.dir / "labels.parquet"
        labeled.to_parquet(p1, index=False)
        ctx.add_output("labels", p1, rows=len(labeled))
        p2 = ctx.dir / "effort.parquet"
        effort.to_parquet(p2, index=False)
        ctx.add_output("effort", p2, rows=len(effort))
        ctx.meta.update({
            "mrds_records_in_bbox": len(mrds),
            "commodity_matches": len(target),
            "deposit_sites": len(deposits),
            "labeled_cells": len(labeled),
            "qa": validate.check_labels(labeled, "labels").to_dict(),
        })

    return run_stage("labels", root / "labels", fingerprint, build, force)


GEOPHYS_GRIDS = {
    "magnetic": "mag_anomaly_nt",
    "gravity_isostatic": "grav_isostatic_mgal",
    "gravity_bouguer": "grav_bouguer_mgal",
}


def stage_geophys(
    profile: Profile, root: Path, acquired: dict[str, AcquiredFile], force: bool
) -> StageOutcome:
    fingerprint = {
        "etl_version": ETL_VERSION,
        "input_shas": {n: acquired[n].sha256 for n in GEOPHYS_GRIDS},
        "h3_res": profile.h3_res,
        "bbox": profile.bbox,
    }

    def build(ctx: StageContext) -> None:
        for name, value_col in GEOPHYS_GRIDS.items():
            grid = readers.read_xyz_grid(acquired[name].path, value_col)
            grid = gridding.bbox_filter(grid, profile.bbox)
            validate.check_frame(
                grid, name, required_cols=("lon", "lat", value_col),
                min_rows=100_000,
                bounds={value_col: (-10_000, 10_000)},  # nT / mGal sanity
            ).raise_if_failed()
            cells = gridding.grid_to_cells(grid, profile.h3_res, value_col)
            path = ctx.dir / f"{name}.parquet"
            cells.to_parquet(path, index=False)
            ctx.add_output(name, path, rows=len(cells))
            ctx.meta[name] = {"nodes": len(grid), "cells": len(cells)}

    return run_stage("geophys", root / "geophys", fingerprint, build, force)


def stage_geochem(
    profile: Profile, root: Path, acquired: dict[str, AcquiredFile], force: bool
) -> StageOutcome:
    src = acquired["ngdb_sediment"]
    fingerprint = {
        "etl_version": ETL_VERSION,
        "input_sha": src.sha256,
        "h3_res": profile.h3_res,
        "bbox": profile.bbox,
        "elements": readers.NGDB_DEFAULT_ELEMENTS,
    }

    def build(ctx: StageContext) -> None:
        samples = readers.read_ngdb_sediment(src.path)
        samples = gridding.bbox_filter(samples, profile.bbox, "latitude", "longitude")
        value_cols = [c for c in samples.columns if c.startswith("geochem_")]
        validate.check_frame(
            samples, "ngdb_sediment",
            required_cols=("latitude", "longitude"),
            min_rows=10_000,
            max_null_frac={c: 0.8 for c in value_cols},
        ).raise_if_failed()
        cells = gridding.points_to_cell_stats(
            samples, profile.h3_res, value_cols,
            count_name="sample_density_geochem",
        )
        path = ctx.dir / "geochem.parquet"
        cells.to_parquet(path, index=False)
        ctx.add_output("geochem", path, rows=len(cells))
        ctx.meta.update({"samples": len(samples), "cells": len(cells)})

    return run_stage("geochem", root / "geochem", fingerprint, build, force)


def stage_extras(
    profile: Profile,
    root: Path,
    extras: list[tuple[AcquiredFile, str, str]],
    force: bool,
) -> StageOutcome | None:
    """Grid every enabled catalog extra onto the profile's cells.

    One parquet per source; the cube stage merges whatever this stage
    produced. A source lands in the geology or effort group purely by the
    covariate name it was given -- the quarantine list in config.py is the
    arbiter, and that is a feature: whoever adds a source decides, visibly,
    which side of the audit it belongs on.
    """
    if not extras:
        return None
    fingerprint = {
        "etl_version": ETL_VERSION,
        "inputs": {a.source: a.sha256 for a, _, _ in extras},
        "kinds": {a.source: (k, v) for a, k, v in extras},
        "h3_res": profile.h3_res,
        "bbox": profile.bbox,
    }

    def build(ctx: StageContext) -> None:
        for acquired, kind, value_name in extras:
            if kind == "xyz_grid":
                grid = readers.read_xyz_grid(acquired.path, value_name)
                grid = gridding.bbox_filter(grid, profile.bbox)
                validate.check_frame(
                    grid, acquired.source,
                    required_cols=("lon", "lat", value_name),
                    min_rows=1_000,
                ).raise_if_failed()
                cells = gridding.grid_to_cells(grid, profile.h3_res, value_name)
            elif kind == "ngdb_geochem":
                samples = readers.read_ngdb_geochem(
                    acquired.path, column_prefix=f"{value_name}_"
                )
                samples = gridding.bbox_filter(samples, profile.bbox, "latitude", "longitude")
                value_cols = [c for c in samples.columns if c.startswith("geochem_")]
                validate.check_frame(
                    samples, acquired.source,
                    required_cols=("latitude", "longitude"),
                    min_rows=1_000,
                ).raise_if_failed()
                cells = gridding.points_to_cell_stats(
                    samples, profile.h3_res, value_cols,
                    count_name=f"sample_density_{value_name}",
                )
            else:
                raise ValueError(f"unknown extra-source kind {kind!r} for {acquired.source}")

            path = ctx.dir / f"{acquired.source}.parquet"
            cells.to_parquet(path, index=False)
            ctx.add_output(acquired.source, path, rows=len(cells))
            ctx.meta[acquired.source] = {"kind": kind, "cells": len(cells)}

    return run_stage("extras", root / "extras", fingerprint, build, force)


def stage_cube_usgs(
    profile: Profile,
    root: Path,
    labels_out: StageOutcome,
    geophys_out: StageOutcome,
    geochem_out: StageOutcome | None,
    extra_out: StageOutcome | None,
    force: bool,
) -> StageOutcome:
    upstream_shas = {
        o["name"]: o["sha256"]
        for out in filter(None, (labels_out, geophys_out, geochem_out, extra_out))
        for o in out.outputs
    }
    fingerprint = {"etl_version": ETL_VERSION, "inputs": upstream_shas, "h3_res": profile.h3_res}

    def build(ctx: StageContext) -> None:
        # Domain = cells the aeromagnetic grid covers. It is the finest and most
        # complete layer; cells outside it have no geophysics at all and would
        # enter the model as pure imputation artifacts.
        cube = pd.read_parquet(geophys_out.output_path("magnetic"))
        for name in ("gravity_isostatic", "gravity_bouguer"):
            cube = cube.merge(pd.read_parquet(geophys_out.output_path(name)), on="h3", how="left")
        if geochem_out is not None:
            cube = cube.merge(pd.read_parquet(geochem_out.output_path("geochem")), on="h3", how="left")
            # Zero samples is a fact about effort, not a measurement gap.
            cube["sample_density_geochem"] = cube["sample_density_geochem"].fillna(0.0)
        if extra_out is not None:
            for o in extra_out.outputs:
                cube = cube.merge(
                    pd.read_parquet(extra_out.output_path(o["name"])), on="h3", how="left"
                )
            # Extras' sample-count columns share the zero-not-missing semantics.
            for col in cube.columns:
                if col.startswith("sample_density_"):
                    cube[col] = cube[col].fillna(0.0)

        effort = pd.read_parquet(labels_out.output_path("effort"))
        cube = cube.merge(effort, on="h3", how="left")
        # Absence of MRDS records is real information (nobody has recorded
        # anything there), not a gap in coverage -- so 0, not imputed median.
        cube["effort_mrds_density"] = cube["effort_mrds_density"].fillna(0.0)

        labeled = pd.read_parquet(labels_out.output_path("labels"))
        cube = attach_labels(cube, labeled)

        centroids = gridding.cell_centroids(cube["h3"])
        cube = cube.merge(centroids, on="h3", how="left")
        cube["dist_to_deposit_km"] = _dist_to_nearest_km(
            cube, cube[cube["deposit"] == 1]
        )

        validate.check_frame(
            cube, "cube",
            required_cols=("h3", "deposit", "lat", "lon"),
            min_rows=5_000, unique_col="h3",
        ).raise_if_failed()

        path = ctx.dir / "cube.parquet"
        cube.to_parquet(path, index=False)
        ctx.add_output("cube", path, rows=len(cube))
        ctx.meta["summary"] = summarize(cube)
        ctx.meta["columns"] = list(cube.columns)

    return run_stage("cube", root / "cube", fingerprint, build, force)


def _dist_to_nearest_km(cube: pd.DataFrame, positives: pd.DataFrame) -> np.ndarray:
    """Great-circle distance from each cell to its nearest *other* labeled cell.

    The strongest effort proxy there is: models trained on it reproduce the
    existing deposit map almost perfectly and discover nothing. Included so the
    bias audit has a worst offender to catch, and named to match the
    quarantine list -- it must never reach the geology model.

    Leave-self-out matters: a labeled cell's distance to the nearest deposit
    is otherwise zero by construction, which turns this column into a copy of
    the label and the audit model into a perfect-scoring fraud. (Found the hard
    way: the first cube shipped PR-AUC 1.000 on the combined model.)
    """
    from sklearn.neighbors import BallTree

    tree = BallTree(np.radians(positives[["lat", "lon"]].to_numpy()), metric="haversine")
    d, _ = tree.query(np.radians(cube[["lat", "lon"]].to_numpy()), k=2)
    is_pos = cube["deposit"].to_numpy() == 1
    # Positives: nearest neighbor is themselves at ~0 km; take the second.
    nearest = np.where(is_pos, d[:, 1], d[:, 0])
    return nearest * 6371.0


def stage_train_publish(
    profile: Profile,
    stages_root: Path,
    runs_root: Storage,
    cube_out: StageOutcome,
    provenance: list[dict],
    force: bool,
) -> StageOutcome:
    """Train, publish the run, and record where it landed.

    The stage output is a local ``published.json`` marker, not the run
    directory: freshness stats output paths relative to the stage directory,
    and a bucket prefix is not one. Consequence: deleting a published run from
    storage no longer makes this stage stale, so republishing needs
    ``--force publish``.
    """
    cfg = Config.from_dict(dict(profile.config_overrides))
    cube_sha = next(o["sha256"] for o in cube_out.outputs if o["name"] == "cube")
    fingerprint = {
        "etl_version": ETL_VERSION,
        "cube_sha": cube_sha,
        "config": cfg.to_dict(),
        "profile": profile.to_dict(),
    }
    # Repointing at a new bucket must republish. Local roots stay out: their
    # absolute path differs between a host checkout and the bind mount, which
    # would make every stage stale on crossing the container boundary.
    if runs_root.protocol != "file":
        fingerprint["runs_root"] = runs_root.uri

    def build(ctx: StageContext) -> None:
        df = pd.read_parquet(cube_out.output_path("cube"))
        clean, features = clean_cube(
            df, cell_col=cfg.cell_col, label_col=cfg.label_col,
            drop_cols=cfg.drop_cols, max_missing_frac=cfg.max_missing_frac,
        )
        summary = summarize(clean, cfg.label_col)
        log.info("cleaned cube: %s / %d covariates", summary, len(features))

        result = run_experiment(clean, features, cfg)
        report = build_report(result, cfg, summary)
        run_dir = publish_run(result, report, cfg, profile.to_dict(), provenance, runs_root)

        from mpm import db

        if db.available():
            full_report = (run_dir / "report.json").read_json()
            db.record_run(
                run_dir.name, profile.name,
                headline=result["runs"]["geology"].pooled,  # type: ignore[index]
                report=full_report,
            )
            db.copy_cells(
                run_dir.name,
                {name: run.scores for name, run in result["runs"].items()},  # type: ignore[union-attr]
            )

        marker = ctx.dir / "published.json"
        marker.write_text(
            json.dumps({"run_id": run_dir.name, "uri": run_dir.uri}, indent=2)
        )
        ctx.add_output("published", marker)
        ctx.meta["run_id"] = run_dir.name
        ctx.meta["run_uri"] = run_dir.uri
        ctx.meta["headline"] = {
            k: result["runs"]["geology"].pooled.get(k)  # type: ignore[index]
            for k in ("capture@5%", "capture@10%", "pr_auc", "roc_auc")
        }

    return run_stage("train_publish", stages_root / "train_publish", fingerprint, build, force)


# --------------------------------------------------------------------- status


def backfill_catalog(data_root: Path) -> int:
    """Register already-published runs in the Postgres catalog.

    For a database attached after runs were published on the filesystem --
    each run directory is self-describing, so the catalog can always be
    rebuilt from the artifacts (never the other way around).
    """
    from mpm import db

    if not db.available():
        raise SystemExit("no DATABASE_URL configured")
    count = 0
    runs_root = runs_storage(data_root)
    for profile_dir in runs_root.ls():
        for run_dir in profile_dir.ls():
            # Doubles as the "is this a run" test: object storage has no
            # directories, so a run is whatever holds a report.
            report_path = run_dir / "report.json"
            if not report_path.exists():
                continue
            report = report_path.read_json()
            db.record_run(
                run_dir.name, profile_dir.name,
                headline=report.get("geology", {}).get("pooled", {}),
                report=report,
            )
            scores_path = run_dir / "scores.parquet"
            if scores_path.exists():
                scores = scores_path.read_parquet()
                db.copy_cells(
                    run_dir.name,
                    {m: g.drop(columns="model") for m, g in scores.groupby("model")},
                )
            count += 1
    return count


def pipeline_status(data_root: Path) -> dict[str, object]:
    """What exists, per profile: stage freshness and the latest published run."""
    out: dict[str, object] = {}
    runs_root = runs_storage(data_root)
    stages_root = data_root / "stages"
    if stages_root.exists():
        for prof_dir in sorted(stages_root.iterdir()):
            stages = {}
            for stage_dir in sorted(prof_dir.iterdir()):
                mpath = stage_dir / "manifest.json"
                if mpath.exists():
                    m = json.loads(mpath.read_text())
                    stages[stage_dir.name] = {
                        "created_utc": m.get("created_utc"),
                        "duration_s": m.get("duration_s"),
                        "outputs": [o["name"] for o in m.get("outputs", [])],
                    }
            latest = runs_root / prof_dir.name / "latest.json"
            out[prof_dir.name] = {
                "stages": stages,
                "latest_run": latest.read_json()["run_id"] if latest.exists() else None,
            }
    return out
