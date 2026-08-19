"""Catalog (Postgres) and orchestration tests.

DB-backed tests need DATABASE_URL and skip without it -- run them in the
compose stack (`docker compose run --rm pipeline pytest`), where postgres is
up. Validation and fallback paths run anywhere.
"""

from __future__ import annotations

import pytest

from mpm import db

# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "name,url,kind,value_name",
    [
        ("Bad Name", "https://mrdata.usgs.gov/x.gz", "xyz_grid", "v_ok"),  # bad name
        ("ok_name", "http://mrdata.usgs.gov/x.gz", "xyz_grid", "v_ok"),  # not https
        ("ok_name", "https://evil.example.com/x.gz", "xyz_grid", "v_ok"),  # bad host
        ("ok_name", "https://mrdata.usgs.gov/x.gz", "shapefile", "v_ok"),  # bad kind
        ("ok_name", "https://mrdata.usgs.gov/x.gz", "xyz_grid", "Bad-Value"),  # bad col
    ],
)
def test_new_source_validation_rejects(name, url, kind, value_name):
    with pytest.raises(db.SourceValidationError):
        db.validate_new_source(name, url, kind, value_name)


def test_new_source_validation_accepts_mrdata():
    db.validate_new_source(
        "ngdb_soil", "https://mrdata.usgs.gov/ngdb/soil/ngdbsoil-csv.zip",
        "ngdb_geochem", "soil",
    )


def test_file_mode_fallbacks(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert not db.available()
    names = {s["name"] for s in db.list_sources()}
    assert "mrds" in names and all(s["builtin"] for s in db.list_sources())
    assert db.extra_sources() == []
    assert db.builtin_enabled("ngdb_sediment") is True
    db.record_run("r", "p", {}, {})  # no-op, must not raise


# ------------------------------------------------------------------ live db

needs_db = pytest.mark.skipif(not db.available(), reason="no DATABASE_URL")


@needs_db
def test_source_roundtrip_and_toggle():
    db.init_schema()
    try:
        db.add_source(
            "test_src_pytest", "https://mrdata.usgs.gov/some/file.xyz.gz",
            "xyz_grid", "test_val", description="test row",
        )
        extras = {s.name: (k, v) for s, k, v in db.extra_sources()}
        assert extras["test_src_pytest"] == ("xyz_grid", "test_val")

        db.set_source_enabled("test_src_pytest", False)
        assert "test_src_pytest" not in {s.name for s, _, _ in db.extra_sources()}

        with pytest.raises(db.SourceValidationError):
            db.set_source_enabled("mrds", False)
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM catalog.sources WHERE name = 'test_src_pytest'")


@needs_db
def test_run_recording_is_idempotent():
    db.init_schema()
    try:
        db.record_run("test_run_pytest", "synth", {"capture@5%": 0.5}, {"a": 1})
        db.record_run("test_run_pytest", "synth", {"capture@5%": 0.9}, {"a": 2})
        runs = [r for r in db.list_runs("synth") if r["run_id"] == "test_run_pytest"]
        assert len(runs) == 1
        assert runs[0]["headline"]["capture@5%"] == 0.5  # first write wins
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM catalog.runs WHERE run_id = 'test_run_pytest'")


# ---------------------------------------------------------------- dagster


def test_dagster_definitions_load():
    dagster = pytest.importorskip("dagster")
    from mpm.orchestration.defs import USGS_PROFILES, defs

    assert set(USGS_PROFILES) == {"us-cu", "us-znpb"}
    job = defs.resolve_job_def("build_prospectivity")
    assert job.partitions_def is not None
    assert set(job.partitions_def.get_partition_keys()) == {"us-cu", "us-znpb"}
    asset_keys = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert {"raw_mrds", "labels", "geophysics", "cube", "prospectivity_run"} <= asset_keys
    del dagster
