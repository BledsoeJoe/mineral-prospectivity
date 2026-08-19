"""Publish a finished experiment as web-servable artifacts.

The contract with the frontend, all JSON, all static, all cacheable:

    runs/<profile>/<run_id>/
      report.json      metrics, audits, config, provenance -- the whole story
      cells.json       one compact record per cell, for the map
      curves.json      capture curves per model, downsampled for plotting
      importance.json  covariate importance with geology/effort grouping
    runs/<profile>/latest.json   {"run_id": ...} pointer

``cells.json`` uses short keys deliberately: at ~30k cells the difference
between {"h3":..,"score":..} and {"h":..,"s":..} is a megabyte on the wire.
Schema: h=h3, s=geology score, u=ensemble spread, c=combined score,
f=fold, y=label, t=hidden truth (synthetic cubes only).
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from mpm.config import Config
from mpm.etl.stages import stable_hash
from mpm.evaluate import capture_curve
from mpm.pipeline import RunResult

log = logging.getLogger("mpm.etl")

CURVE_POINTS = 256


def _round(x: float, nd: int = 4) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def _cells_records(geology: RunResult, combined: RunResult, label_col: str) -> list[dict]:
    g = geology.scores
    c = combined.scores.set_index("h3")["score"]
    records = []
    has_truth = "truth_deposit" in g.columns
    for row in g.itertuples(index=False):
        rec = {
            "h": row.h3,
            "s": _round(row.score),
            "u": _round(row.score_std),
            "c": _round(c.get(row.h3)),
            "f": int(row.fold),
            "y": int(getattr(row, label_col)),
        }
        if has_truth:
            rec["t"] = int(row.truth_deposit)
        records.append(rec)
    return records


def _curves(runs: dict[str, RunResult], label_col: str) -> dict:
    out: dict[str, object] = {}
    for name, run in runs.items():
        s = run.scores.dropna(subset=["score"])
        area, captured = capture_curve(s[label_col].to_numpy(), s["score"].to_numpy())
        idx = np.unique(
            np.clip((np.linspace(0, 1, CURVE_POINTS) * (len(area) - 1)).astype(int), 0, len(area) - 1)
        )
        out[name] = {
            "area": [round(float(a), 5) for a in area[idx]],
            "captured": [round(float(v), 5) for v in captured[idx]],
        }
    return out


def _importance(result: dict[str, object]) -> dict:
    effort = set(result["effort_features"])  # type: ignore[arg-type]
    out = {}
    for name, run in result["runs"].items():  # type: ignore[union-attr]
        ranked = sorted(run.importance.items(), key=lambda kv: -kv[1])
        out[name] = [
            {"feature": f, "importance": round(v, 5), "group": "effort" if f in effort else "geology"}
            for f, v in ranked if v > 0
        ][:25]
    return out


def publish_run(
    result: dict[str, object],
    report: dict,
    cfg: Config,
    profile_dict: dict,
    provenance: list[dict],
    runs_root: Path,
) -> Path:
    """Write one immutable run directory and repoint ``latest.json`` at it."""
    runs = result["runs"]  # type: ignore[assignment]
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + stable_hash(report)[:8]
    outdir = runs_root / run_id
    outdir.mkdir(parents=True, exist_ok=False)

    label_col = cfg.label_col
    full_report = {
        "run_id": run_id,
        "profile": profile_dict,
        "provenance": provenance,
        **report,
    }
    (outdir / "report.json").write_text(json.dumps(full_report, indent=2, default=float))

    cells = _cells_records(runs["geology"], runs["combined"], label_col)
    with gzip.open(outdir / "cells.json.gz", "wt") as f:
        json.dump(cells, f, separators=(",", ":"))

    (outdir / "curves.json").write_text(json.dumps(_curves(runs, label_col), default=float))
    (outdir / "importance.json").write_text(json.dumps(_importance(result), default=float))

    # Machine-friendly copy of the full per-cell table, uncompacted.
    pd.concat(
        {name: run.scores.set_index("h3") for name, run in runs.items()},
        names=["model", "h3"],
    ).reset_index().to_parquet(outdir / "scores.parquet", index=False)

    tmp = runs_root / "latest.json.tmp"
    tmp.write_text(json.dumps({"run_id": run_id}))
    tmp.replace(runs_root / "latest.json")
    log.info("published run %s (%d cells)", run_id, len(cells))
    return outdir
