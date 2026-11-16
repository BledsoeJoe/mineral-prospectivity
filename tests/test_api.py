"""API contract tests against a fabricated runs directory."""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    run_dir = tmp_path / "runs" / "synth" / "20260101T000000Z-abc12345"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps({
        "run_id": "20260101T000000Z-abc12345",
        "profile": {"description": "test profile"},
        "cube": {"cells": 10, "positives": 2},
        "geology": {"pooled": {"capture@5%": 0.5}},
    }))
    (run_dir / "curves.json").write_text("{}")
    (run_dir / "importance.json").write_text("{}")
    with gzip.open(run_dir / "cells.json.gz", "wt") as f:
        json.dump([{"h": "abc", "s": 0.1, "u": 0.0, "f": 0, "y": 0}], f)
    (tmp_path / "runs" / "synth" / "latest.json").write_text(
        json.dumps({"run_id": "20260101T000000Z-abc12345"})
    )

    from mpm import api
    from mpm.storage import open_storage

    monkeypatch.setattr(api, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(api, "RUNS_ROOT", open_storage(tmp_path / "runs"))
    return TestClient(api.app)


@pytest.fixture()
def remote_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-local runs root, which the public role requires."""
    import fsspec

    from mpm import api
    from mpm.storage import Storage

    monkeypatch.setattr(
        api, "RUNS_ROOT", Storage(fsspec.filesystem("memory"), "/bucket/runs", "memory")
    )


def test_health_lists_profiles(client: TestClient):
    body = client.get("/api/health").json()
    assert body["ok"] and body["profiles"] == ["synth"]


def test_full_role_advertises_controls(client: TestClient):
    body = client.get("/api/health").json()
    assert body["role"] == "all"
    assert body["capabilities"]["materialize"] and body["capabilities"]["dagster_ui"]


def test_public_role_serves_reads_only(remote_runs, monkeypatch: pytest.MonkeyPatch):
    """Any non-GET route on the public node is a route back to the private side.

    Asserted over the whole route table, so a write endpoint added to the
    wrong router later fails here too.
    """
    from mpm import api

    monkeypatch.setenv("MPM_API_ROLE", "public")
    app = api.create_app()
    assert {m for r in app.routes for m in getattr(r, "methods", ())} <= {"GET", "HEAD"}

    body = TestClient(app).get("/api/health").json()
    assert body["role"] == "public"
    assert not any(body["capabilities"].values())
    # The bucket path stays private; only the backend kind is reported.
    assert body["runs_root"] == "memory"


def test_public_role_refuses_local_runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Otherwise a missing MPM_RUNS_ROOT looks like "nothing published yet"."""
    from mpm import api
    from mpm.storage import open_storage

    monkeypatch.setattr(api, "RUNS_ROOT", open_storage(tmp_path / "runs"))
    with pytest.raises(RuntimeError, match="MPM_RUNS_ROOT"):
        api.create_app("public")


MODEL_MODULES = ("numpy", "pandas", "pyarrow", "sklearn", "lightgbm", "h3", "yaml", "matplotlib")


def test_api_runs_without_the_model_extra():
    """The api image installs no [model] deps, but this test image has them
    all, so a stray import would pass every other test and crash in prod.

    Runs in a subprocess with those modules blocked (a ``None`` entry in
    ``sys.modules`` makes importing it raise), and hits the handlers that
    import lazily: the source catalog and materialize's profile lookup.
    """
    code = f"""
import sys
for name in {MODEL_MODULES!r}:
    sys.modules[name] = None
from fastapi.testclient import TestClient
from mpm import api
client = TestClient(api.create_app("all"))
assert client.get("/api/sources").status_code == 200
assert client.post("/api/materialize", json={{"profile": "synth"}}).status_code == 422
"""
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr


def test_unknown_role_is_refused(remote_runs):
    """A typo must not fall back to the default, which is the role with writes."""
    from mpm import api

    with pytest.raises(ValueError, match="MPM_API_ROLE"):
        api.create_app("pubilc")


def test_profiles_carry_headline(client: TestClient):
    (p,) = client.get("/api/profiles").json()
    assert p["latest_run"] == "20260101T000000Z-abc12345"
    assert p["headline"]["capture@5%"] == 0.5


def test_latest_resolves_pointer(client: TestClient):
    body = client.get("/api/profiles/synth/runs/latest/report").json()
    assert body["run_id"] == "20260101T000000Z-abc12345"


def test_cells_served_pre_gzipped(client: TestClient):
    res = client.get("/api/profiles/synth/runs/latest/cells")
    assert res.status_code == 200
    # httpx transparently decompresses; the payload must round-trip.
    assert res.json()[0]["h"] == "abc"


def test_cells_streamed_not_read_whole(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Artifacts must stream. Round-tripping passes either way, so this pins
    the mechanism instead."""
    from mpm.storage import Storage

    def _forbidden(self: Storage) -> bytes:
        raise AssertionError(f"{self.name} was read whole instead of streamed")

    monkeypatch.setattr(Storage, "read_bytes", _forbidden)
    run = "20260101T000000Z-abc12345"
    for artifact in ("cells", "report", "curves", "importance"):
        res = client.get(f"/api/profiles/synth/runs/{run}/{artifact}")
        assert res.status_code == 200, artifact


def test_run_listing_excludes_the_latest_pointer(client: TestClient):
    """latest.json sits beside the run directories and is not a run."""
    assert client.get("/api/profiles/synth/runs").json() == ["20260101T000000Z-abc12345"]


def test_unknown_run_404s(client: TestClient):
    assert client.get("/api/profiles/synth/runs/nope/report").status_code == 404
    assert client.get("/api/profiles/ghost/runs/latest/report").status_code == 404


def test_path_traversal_rejected(client: TestClient):
    assert client.get("/api/profiles/..%2F..%2Fetc/runs/latest/report").status_code in (404, 422)


def test_sources_fallback_without_db(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    body = client.get("/api/sources").json()
    assert body["catalog_db"] is False
    assert {s["name"] for s in body["sources"]} >= {"mrds", "magnetic"}


def test_add_source_needs_db(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    res = client.post("/api/sources", json={
        "name": "ngdb_soil", "url": "https://mrdata.usgs.gov/ngdb/soil/ngdbsoil-csv.zip",
        "kind": "ngdb_geochem", "value_name": "soil",
    })
    assert res.status_code == 503


def test_add_source_shape_validation(client: TestClient):
    res = client.post("/api/sources", json={
        "name": "Bad Name!", "url": "x", "kind": "shapefile", "value_name": "v",
    })
    assert res.status_code == 422


def test_sources_include_unregistered_suggestions(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    body = client.get("/api/sources").json()
    names = {s["name"] for s in body["suggestions"]}
    assert "ngdb_soil" in names and "ngdb_concentrate" in names
    # every suggestion must be addable as-is through the POST contract
    for s in body["suggestions"]:
        assert s["kind"] in body["kinds"]


def test_materialize_rejects_non_usgs_profile(client: TestClient):
    assert client.post("/api/materialize", json={"profile": "synth"}).status_code == 422
    assert client.post("/api/materialize", json={"profile": "nope"}).status_code == 422


def test_materialize_503_when_dagster_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MPM_DAGSTER_URL", "http://127.0.0.1:1/dagster")
    res = client.post("/api/materialize", json={"profile": "us-cu"})
    assert res.status_code == 503
    assert "unreachable" in res.json()["detail"]
