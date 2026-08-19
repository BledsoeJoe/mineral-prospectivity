"""Read-only HTTP API over published pipeline runs.

Deliberately thin: the pipeline publishes static JSON artifacts, and this
service does little more than list them and stream them with the right
headers. All the expensive thinking happened at publish time; the API's job
is to be boring, fast, and impossible to break by asking a weird question.

    MPM_DATA_ROOT=data uvicorn mpm.api:app

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

import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from mpm import db

DATA_ROOT = Path(os.environ.get("MPM_DATA_ROOT", "data"))
RUNS_ROOT = DATA_ROOT / "runs"

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


def _run_dir(profile: str, run_id: str) -> Path:
    _safe(profile, "profile")
    _safe(run_id, "run id")
    if run_id == "latest":
        pointer = RUNS_ROOT / profile / "latest.json"
        if not pointer.exists():
            raise HTTPException(404, f"profile {profile!r} has no published runs")
        run_id = json.loads(pointer.read_text())["run_id"]
    d = RUNS_ROOT / profile / run_id
    if not d.is_dir():
        raise HTTPException(404, f"run {profile}/{run_id} not found")
    return d


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "data_root": str(DATA_ROOT),
        "profiles": _profiles(),
        "catalog_db": db.available(),
    }


def _profiles() -> list[str]:
    if not RUNS_ROOT.exists():
        return []
    return sorted(p.name for p in RUNS_ROOT.iterdir() if (p / "latest.json").exists())


@app.get("/api/profiles")
def profiles() -> list[dict]:
    out = []
    for name in _profiles():
        latest = json.loads((RUNS_ROOT / name / "latest.json").read_text())["run_id"]
        report = json.loads((RUNS_ROOT / name / latest / "report.json").read_text())
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
    root = RUNS_ROOT / profile
    if not root.is_dir():
        raise HTTPException(404, f"profile {profile!r} not found")
    return sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True)


@app.get("/api/profiles/{profile}/runs/{run_id}/report")
def report(profile: str, run_id: str) -> FileResponse:
    return FileResponse(_run_dir(profile, run_id) / "report.json", media_type="application/json")


@app.get("/api/profiles/{profile}/runs/{run_id}/curves")
def curves(profile: str, run_id: str) -> FileResponse:
    return FileResponse(_run_dir(profile, run_id) / "curves.json", media_type="application/json")


@app.get("/api/profiles/{profile}/runs/{run_id}/importance")
def importance(profile: str, run_id: str) -> FileResponse:
    return FileResponse(_run_dir(profile, run_id) / "importance.json", media_type="application/json")


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
def cells(profile: str, run_id: str) -> Response:
    """The map payload. Stored gzipped; served gzipped -- every browser
    inflates transparently, and the server never spends CPU recompressing."""
    path = _run_dir(profile, run_id) / "cells.json.gz"
    if not path.exists():
        raise HTTPException(404, "cells artifact missing for this run")
    return Response(
        content=path.read_bytes(),
        media_type="application/json",
        headers={"Content-Encoding": "gzip", "Cache-Control": "public, max-age=3600"},
    )
