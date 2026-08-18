"""API contract tests against a fabricated runs directory."""

from __future__ import annotations

import gzip
import json
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

    monkeypatch.setattr(api, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(api, "RUNS_ROOT", tmp_path / "runs")
    return TestClient(api.app)


def test_health_lists_profiles(client: TestClient):
    body = client.get("/api/health").json()
    assert body["ok"] and body["profiles"] == ["synth"]


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
