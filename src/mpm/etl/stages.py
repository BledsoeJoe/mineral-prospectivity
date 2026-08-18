"""Stage execution with manifest-based freshness.

A stage is a pure-ish function from (inputs on disk, config) to (outputs on
disk). Each stage directory carries a ``manifest.json`` recording a fingerprint
of everything that could change the result -- input file hashes, the relevant
slice of profile config, and a per-stage code version bumped on breaking logic
changes. On the next run, a stage whose fingerprint matches and whose outputs
still exist is skipped.

This is deliberately file-based rather than a workflow engine. The point is the
*pattern* -- content-addressed freshness, atomic writes, provenance you can read
with ``cat`` -- which ports directly onto Prefect/Airflow/Dagster when a real
scheduler is warranted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("mpm.etl")

MANIFEST_NAME = "manifest.json"


def stable_hash(obj: Any) -> str:
    """Deterministic hash of a JSON-serialisable object."""
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def file_info(path: Path, sha: str | None = None, **extra: Any) -> dict[str, Any]:
    """Provenance record for one produced or consumed file."""
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha or file_sha256(path),
        **extra,
    }


@dataclass
class StageOutcome:
    name: str
    stage_dir: Path
    skipped: bool
    duration_s: float
    manifest: dict[str, Any]

    @property
    def outputs(self) -> list[dict[str, Any]]:
        return self.manifest.get("outputs", [])

    def output_path(self, name: str) -> Path:
        for o in self.outputs:
            if o.get("name") == name:
                return (self.stage_dir / o["path"]).resolve()
        raise KeyError(f"stage {self.name!r} has no output named {name!r}")


@dataclass
class StageContext:
    """Handed to a stage builder: where to write, and how to report.

    Output paths are stored *relative to the stage directory*, so a data root
    can move between machines -- or between a host checkout and a container
    bind mount -- without every manifest going stale."""

    dir: Path
    outputs: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def add_output(self, name: str, path: Path, **extra: Any) -> Path:
        info = file_info(path, **extra)
        info["path"] = os.path.relpath(path, self.dir)
        self.outputs.append({"name": name, **info})
        return path


def load_outcome(name: str, stage_dir: Path) -> StageOutcome:
    """Rehydrate a StageOutcome from an existing manifest.

    Used by orchestrators (Dagster assets) whose steps run in separate
    processes: a downstream asset reloads its upstream stage's outcome from
    disk instead of receiving it in memory.
    """
    manifest_path = stage_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"stage {name!r} has no manifest at {manifest_path}; materialize it first"
        )
    manifest = json.loads(manifest_path.read_text())
    return StageOutcome(name, stage_dir, skipped=True, duration_s=0.0, manifest=manifest)


def run_stage(
    name: str,
    stage_dir: Path,
    fingerprint: dict[str, Any],
    builder: Callable[[StageContext], None],
    force: bool = False,
) -> StageOutcome:
    """Run ``builder`` unless a fresh manifest says its work is already done.

    Freshness == the stored fingerprint matches *and* every recorded output
    still exists. A crashed run cannot leave a half-written stage marked fresh,
    because the manifest is written only after the builder returns; a stale or
    forced run rebuilds into place after clearing the previous outputs.
    """
    stage_dir.mkdir(parents=True, exist_ok=True)
    fp_hash = stable_hash(fingerprint)
    manifest_path = stage_dir / MANIFEST_NAME

    if not force and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        outputs_exist = all(
            (stage_dir / o["path"]).exists() for o in manifest.get("outputs", [])
        )
        if manifest.get("fingerprint") == fp_hash and outputs_exist:
            log.info("stage %-10s fresh, skipping", name)
            return StageOutcome(name, stage_dir, skipped=True, duration_s=0.0, manifest=manifest)
        reason = "fingerprint changed" if manifest.get("fingerprint") != fp_hash else "outputs missing"
        log.info("stage %-10s stale (%s), rebuilding", name, reason)
    else:
        log.info("stage %-10s %s", name, "forced" if force else "no manifest, building")

    # Clear previous outputs so a rebuild never mixes old and new artifacts.
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        for o in old.get("outputs", []):
            p = (stage_dir / o["path"]).resolve()
            if p.exists() and stage_dir.resolve() in p.parents:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        manifest_path.unlink()

    ctx = StageContext(dir=stage_dir)
    t0 = time.monotonic()
    builder(ctx)
    duration = time.monotonic() - t0

    manifest = {
        "stage": name,
        "fingerprint": fp_hash,
        "fingerprint_inputs": fingerprint,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_s": round(duration, 2),
        "outputs": ctx.outputs,
        "meta": ctx.meta,
    }
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, default=str))
    tmp.replace(manifest_path)
    log.info("stage %-10s done in %.1fs", name, duration)
    return StageOutcome(name, stage_dir, skipped=False, duration_s=duration, manifest=manifest)
