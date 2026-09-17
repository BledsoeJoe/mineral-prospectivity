"""Read-only HTTP API over published pipeline runs.

Deliberately thin: the pipeline publishes static JSON artifacts, and this
service does little more than list them and stream them with the right
headers. All the expensive thinking happened at publish time; the API's job
is to be boring, fast, and impossible to break by asking a weird question.

Artifacts are addressed through :mod:`mpm.storage`, so this serves a local
directory or an S3 prefix unchanged. Every artifact response streams in
bounded chunks rather than loading the body, which keeps memory flat in the
size of the cube.

    MPM_DATA_ROOT=data uvicorn mpm.api:app
    MPM_RUNS_ROOT=s3://bucket/prefix/runs uvicorn mpm.api:app

Endpoints
    GET /api/health
    GET /api/profiles                                  published profiles + latest run
    GET /api/profiles/{profile}/runs                   run history
    GET /api/profiles/{profile}/runs/{run}/report      full report json
    GET /api/profiles/{profile}/runs/{run}/cells       per-cell map payload (pre-gzipped)
    GET /api/profiles/{profile}/runs/{run}/curves      capture curves
    GET /api/profiles/{profile}/runs/{run}/importance  covariate importance

    GET   /api/sources          the data-source catalog (Postgres, or builtin fallback)
    POST  /api/sources          register a new source; picked up on the next pipeline run
    PATCH /api/sources/{name}   enable / disable a source

``{run}`` may be the literal ``latest``.
"""

from __future__ import annotations

import os
import re

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mpm import db
from mpm.storage import Storage, runs_storage

DATA_ROOT = os.environ.get("MPM_DATA_ROOT", "data")
RUNS_ROOT = runs_storage()

_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")

app = FastAPI(title="Mineral prospectivity API", version="0.2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)


def _safe(name: str, what: str) -> str:
    if not _SAFE.match(name):
        raise HTTPException(422, f"invalid {what} {name!r}")
    return name


def _run_dir(profile: str, run_id: str) -> Storage:
    """Resolve a run directory. Existence is left to the artifact read, which
    has to handle a missing object anyway and costs one request instead of
    a LIST."""
    _safe(profile, "profile")
    _safe(run_id, "run id")
    if run_id == "latest":
        pointer = RUNS_ROOT / profile / "latest.json"
        if not pointer.exists():
            raise HTTPException(404, f"profile {profile!r} has no published runs")
        run_id = pointer.read_json()["run_id"]
    return RUNS_ROOT / profile / run_id


def _stream(artifact: Storage, media_type: str, **headers: str) -> StreamingResponse:
    """Stream an artifact in bounded chunks rather than buffering it."""
    try:
        size = artifact.size()
    except FileNotFoundError:
        raise HTTPException(404, f"{artifact.name} not found for this run") from None
    return StreamingResponse(
        artifact.iter_chunks(),
        media_type=media_type,
        headers={"Content-Length": str(size), **headers},
    )


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "data_root": str(DATA_ROOT),
        "runs_root": RUNS_ROOT.uri,
        "profiles": _profiles(),
        "catalog_db": db.available(),
    }


def _profiles() -> list[str]:
    return sorted(p.name for p in RUNS_ROOT.ls() if (p / "latest.json").exists())


@app.get("/api/profiles")
def profiles() -> list[dict]:
    out = []
    for name in _profiles():
        latest = (RUNS_ROOT / name / "latest.json").read_json()["run_id"]
        report = (RUNS_ROOT / name / latest / "report.json").read_json()
        out.append({
            "profile": name,
            "latest_run": latest,
            "description": report.get("profile", {}).get("description", ""),
            "cube": report.get("cube", {}),
            "headline": report.get("geology", {}).get("pooled", {}),
            "has_truth": "discovery_audit" in report,
        })
    return out


@app.get("/api/profiles/{profile}/runs")
def runs(profile: str) -> list[str]:
    _safe(profile, "profile")
    children = (RUNS_ROOT / profile).ls()
    if not children:
        raise HTTPException(404, f"profile {profile!r} not found")
    # latest.json sits beside the run directories; it is a pointer, not a run.
    return sorted((p.name for p in children if p.name != "latest.json"), reverse=True)


@app.get("/api/profiles/{profile}/runs/{run_id}/report")
def report(profile: str, run_id: str) -> StreamingResponse:
    return _stream(_run_dir(profile, run_id) / "report.json", "application/json")


@app.get("/api/profiles/{profile}/runs/{run_id}/curves")
def curves(profile: str, run_id: str) -> StreamingResponse:
    return _stream(_run_dir(profile, run_id) / "curves.json", "application/json")


@app.get("/api/profiles/{profile}/runs/{run_id}/importance")
def importance(profile: str, run_id: str) -> StreamingResponse:
    return _stream(_run_dir(profile, run_id) / "importance.json", "application/json")


# ------------------------------------------------------------ source catalog


class SourceIn(BaseModel):
    """A user-registered upstream dataset.

    Validation is enforced again in mpm.db (name shape, https, host
    allowlist); this model is the API-shaped subset.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    url: str = Field(max_length=500)
    kind: str = Field(pattern="^(xyz_grid|ngdb_geochem)$")
    value_name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    description: str = Field(default="", max_length=2000)
    citation: str = Field(default="", max_length=500)


class SourceToggle(BaseModel):
    enabled: bool


@app.get("/api/sources")
def sources() -> dict:
    from mpm.etl.sources import SUGGESTED_SOURCES

    registered = db.list_sources()
    taken = {s["name"] for s in registered}
    return {
        "catalog_db": db.available(),
        "allowed_hosts": list(db.allowed_hosts()),
        "kinds": list(db.EXTRA_KINDS),
        "sources": registered,
        # Curated, verified-parseable additions not yet in the catalog.
        "suggestions": [s for s in SUGGESTED_SOURCES if s["name"] not in taken],
    }


@app.post("/api/sources", status_code=201)
def add_source(body: SourceIn) -> dict:
    if not db.available():
        raise HTTPException(
            503, "no catalog database configured; source management needs the postgres service"
        )
    try:
        created = db.add_source(
            body.name, body.url, body.kind, body.value_name, body.description, body.citation
        )
    except db.SourceValidationError as e:
        raise HTTPException(422, str(e)) from e
    return {**created, "note": "included in the next pipeline run / asset materialization"}


@app.patch("/api/sources/{name}")
def toggle_source(name: str, body: SourceToggle) -> dict:
    _safe(name, "source")
    if not db.available():
        raise HTTPException(503, "no catalog database configured")
    try:
        db.set_source_enabled(name, body.enabled)
    except db.SourceValidationError as e:
        raise HTTPException(422, str(e)) from e
    except KeyError:
        raise HTTPException(404, f"source {name!r} not found") from None
    return {"name": name, "enabled": body.enabled}


# -------------------------------------------------------------- orchestration


class MaterializeIn(BaseModel):
    profile: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")


@app.post("/api/materialize", status_code=202)
def materialize(body: MaterializeIn) -> dict:
    """Launch a Dagster run that rebuilds one profile's cube and republishes.

    Goes through Dagster's GraphQL API rather than running the pipeline here:
    the run gets lineage, logs and history in the Dagster UI, and this
    container keeps its read-only mount. Stage manifests make the run cheap
    when nothing actually changed.
    """
    from mpm.etl.profiles import PROFILES
    from mpm.orchestration import client

    profile = PROFILES.get(body.profile)
    if profile is None or profile.kind != "usgs":
        usgs = sorted(p.name for p in PROFILES.values() if p.kind == "usgs")
        raise HTTPException(422, f"profile must be one of {usgs}")
    try:
        run_id = client.launch_partition(body.profile)
    except client.DagsterUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except client.LaunchFailed as e:
        raise HTTPException(502, f"dagster refused the launch: {e}") from e
    return {
        "run_id": run_id,
        "profile": body.profile,
        "watch_url": f"/dagster/runs/{run_id}",
    }


# ------------------------------------------------------------------ map data


@app.get("/api/profiles/{profile}/runs/{run_id}/cells")
def cells(profile: str, run_id: str) -> StreamingResponse:
    """The map payload. Stored gzipped; served gzipped -- every browser
    inflates transparently, and the server never spends CPU recompressing.
    The largest artifact here, and the reason responses stream."""
    return _stream(
        _run_dir(profile, run_id) / "cells.json.gz",
        "application/json",
        **{"Content-Encoding": "gzip", "Cache-Control": "public, max-age=3600"},
    )
