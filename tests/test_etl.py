"""Tests for the ingest pipeline, aimed at its silent-failure paths: stale
stages that pretend to be fresh, censored geochemistry parsed as negative
concentrations, checksum drift accepted quietly, and the dist-to-deposit
label leak that made the first real-data run report PR-AUC 1.000."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mpm.config import DEFAULT_EFFORT_PATTERNS, Config
from mpm.etl import gridding, validate
from mpm.etl.acquire import ChecksumMismatch, _check_pin, acquire
from mpm.etl.flow import _dist_to_nearest_km
from mpm.etl.profiles import PROFILES
from mpm.etl.readers import read_xyz_grid
from mpm.etl.sources import Source
from mpm.etl.stages import StageContext, run_stage, stable_hash
from mpm.evaluate import split_feature_groups

# -------------------------------------------------------------------- stages


def _write_stage(ctx: StageContext) -> None:
    p = ctx.dir / "out.txt"
    p.write_text("v1")
    ctx.add_output("out", p)


def test_stage_skips_when_fresh(tmp_path: Path):
    fp = {"version": 1}
    first = run_stage("s", tmp_path / "s", fp, _write_stage)
    second = run_stage("s", tmp_path / "s", fp, _write_stage)
    assert not first.skipped and second.skipped


def test_stage_rebuilds_on_fingerprint_change(tmp_path: Path):
    run_stage("s", tmp_path / "s", {"version": 1}, _write_stage)
    second = run_stage("s", tmp_path / "s", {"version": 2}, _write_stage)
    assert not second.skipped


def test_stage_rebuilds_when_output_deleted(tmp_path: Path):
    first = run_stage("s", tmp_path / "s", {"version": 1}, _write_stage)
    first.output_path("out").unlink()
    second = run_stage("s", tmp_path / "s", {"version": 1}, _write_stage)
    assert not second.skipped


def test_crashed_stage_is_not_fresh(tmp_path: Path):
    """The manifest is written after the builder; a mid-build crash must leave
    the stage stale, half-written outputs and all."""

    def crashes(ctx: StageContext) -> None:
        (ctx.dir / "partial.txt").write_text("half")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_stage("s", tmp_path / "s", {"version": 1}, crashes)
    rerun = run_stage("s", tmp_path / "s", {"version": 1}, _write_stage)
    assert not rerun.skipped


def test_stable_hash_ignores_key_order():
    assert stable_hash({"a": 1, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": 1})


# ------------------------------------------------------------------- acquire


def test_acquire_uses_cache_and_verifies_pin(tmp_path: Path):
    dest = tmp_path / "file.bin"
    dest.write_bytes(b"cached content")
    src = Source(name="x", url="https://invalid.example/none", filename="file.bin",
                 description="", citation="")
    got = acquire(src, tmp_path)  # would raise if it tried the network
    assert got.from_cache and got.path == dest


def test_checksum_pin_mismatch_raises():
    src = Source(name="x", url="u", filename="f", description="", citation="",
                 sha256="0" * 64)
    with pytest.raises(ChecksumMismatch):
        _check_pin(src, "f" * 64)


# ------------------------------------------------------------------- readers


def test_xyz_grid_reader_drops_bad_rows(tmp_path: Path):
    p = tmp_path / "grid.xyz.gz"
    with gzip.open(p, "wt") as f:
        f.write("  -100.0  40.0  12.5\n  -100.1  40.0  nan\n  -400.0  40.0  1.0\n")
    df = read_xyz_grid(p, "val")
    assert len(df) == 1
    assert df.val.iloc[0] == 12.5


# ------------------------------------------------------------------ gridding


def test_grid_to_cells_mean_and_std():
    df = pd.DataFrame({"lat": [40.0, 40.0001, 45.0], "lon": [-100.0, -100.0001, -110.0],
                       "v": [1.0, 3.0, 5.0]})
    out = gridding.grid_to_cells(df, res=4, value_col="v")
    assert len(out) == 2
    near = out[out.v_mean == 2.0]
    assert len(near) == 1 and near.v_std.iloc[0] > 0
    lone = out[out.v_mean == 5.0]
    assert lone.v_std.iloc[0] == 0.0  # single node: spread 0, not NaN


def test_dist_to_nearest_is_leave_self_out():
    """A labeled cell's distance must be to the nearest OTHER deposit, never
    0-to-itself -- otherwise the column is the label in disguise."""
    cube = pd.DataFrame({
        "lat": [40.0, 41.0, 42.0], "lon": [-100.0, -100.0, -100.0],
        "deposit": [1, 1, 0],
    })
    d = _dist_to_nearest_km(cube, cube[cube.deposit == 1])
    assert d[0] > 100  # ~111 km to the other positive, not 0 to itself
    assert d[1] > 100
    assert d[2] == pytest.approx(111, rel=0.02)


# ------------------------------------------------------------------ validate


def test_validation_fails_loudly_on_missing_columns():
    df = pd.DataFrame({"a": [1]})
    with pytest.raises(validate.ValidationError, match="required_columns"):
        validate.check_frame(df, "t", required_cols=("a", "b")).raise_if_failed()


def test_validation_bounds_check():
    df = pd.DataFrame({"latitude": [45.0, 95.0]})
    report = validate.check_frame(df, "t", bounds={"latitude": (-90, 90)})
    with pytest.raises(validate.ValidationError, match="bounds"):
        report.raise_if_failed()


def test_validation_warnings_do_not_block():
    df = pd.DataFrame({"a": [1.0, np.nan, np.nan, np.nan]})
    validate.check_frame(df, "t", max_null_frac={"a": 0.5}).raise_if_failed()


def test_label_gate_names_the_fix():
    few = pd.DataFrame({"h3": ["85283473fffffff"]})
    with pytest.raises(validate.ValidationError, match="commodity"):
        validate.check_labels(few, "t", min_positives=30).raise_if_failed()


# ---------------------------------------------------------------- quarantine


def test_isna_indicators_are_quarantined():
    """Survey-extent indicators must land in the effort group: 'this cell was
    never sampled' is a fact about people, not rocks."""
    geology, effort = split_feature_groups(
        ["geochem_cu_log_ppm", "geochem_cu_log_ppm_isna", "mag_anomaly_nt_mean",
         "sample_density_geochem", "dist_to_deposit_km", "effort_mrds_density"],
        DEFAULT_EFFORT_PATTERNS,
    )
    assert geology == ["geochem_cu_log_ppm", "mag_anomaly_nt_mean"]
    assert set(effort) == {
        "geochem_cu_log_ppm_isna", "sample_density_geochem",
        "dist_to_deposit_km", "effort_mrds_density",
    }


def test_profiles_produce_valid_configs():
    for profile in PROFILES.values():
        cfg = Config.from_dict(dict(profile.config_overrides))
        assert cfg.split.n_folds >= 2


# ------------------------------------------------------------------ manifest


def test_manifest_records_provenance(tmp_path: Path):
    out = run_stage("s", tmp_path / "s", {"v": 1}, _write_stage)
    manifest = json.loads((tmp_path / "s" / "manifest.json").read_text())
    assert manifest["fingerprint"] == out.manifest["fingerprint"]
    assert manifest["outputs"][0]["sha256"]
    assert manifest["fingerprint_inputs"] == {"v": 1}
