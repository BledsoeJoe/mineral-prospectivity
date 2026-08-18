"""Data-quality gates between stages.

A pipeline that only fails on exceptions fails too late: pandas will cheerfully
build a cube from a half-parsed file, and the first symptom is a subtly wrong
map three stages later. These checks make the pipeline fail at the stage where
the problem is, with a message naming the file and the number.

Two severities:

* ``fatal`` -- the stage's output would be wrong; raise and stop.
* ``warn``  -- worth a human's attention but the run can proceed (e.g. a
  covariate with thin coverage that clean_cube will drop anyway).

Every report is JSON-serialisable and lands in the stage manifest, so QA
history rides along with the artifacts it describes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

log = logging.getLogger("mpm.etl")


class ValidationError(RuntimeError):
    pass


@dataclass
class Check:
    name: str
    ok: bool
    fatal: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "ok": self.ok, "fatal": self.fatal, "detail": self.detail}


@dataclass
class Report:
    subject: str
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, fatal: bool = True) -> None:
        self.checks.append(Check(name, ok, fatal, detail))
        if not ok:
            (log.error if fatal else log.warning)("QA %s / %s: %s", self.subject, name, detail)

    def raise_if_failed(self) -> Report:
        failed = [c for c in self.checks if not c.ok and c.fatal]
        if failed:
            lines = "; ".join(f"{c.name}: {c.detail}" for c in failed)
            raise ValidationError(f"{self.subject}: {lines}")
        return self

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "passed": all(c.ok or not c.fatal for c in self.checks),
            "checks": [c.to_dict() for c in self.checks],
        }


def check_frame(
    df: pd.DataFrame,
    subject: str,
    required_cols: tuple[str, ...] = (),
    min_rows: int = 1,
    max_rows: int | None = None,
    unique_col: str | None = None,
    bounds: dict[str, tuple[float, float]] | None = None,
    max_null_frac: dict[str, float] | None = None,
) -> Report:
    """Structural checks that apply to any tabular stage output."""
    r = Report(subject)

    missing = [c for c in required_cols if c not in df.columns]
    r.add("required_columns", not missing, f"missing {missing}" if missing else "all present")

    n = len(df)
    r.add("min_rows", n >= min_rows, f"{n} rows (need >= {min_rows})")
    if max_rows is not None:
        # An order-of-magnitude ceiling catches format drift (e.g. a delimiter
        # change exploding one row into many) rather than data growth.
        r.add("max_rows", n <= max_rows, f"{n} rows (ceiling {max_rows})", fatal=False)

    if unique_col and unique_col in df.columns:
        dups = int(df[unique_col].duplicated().sum())
        r.add(f"unique[{unique_col}]", dups == 0, f"{dups} duplicate values")

    for col, (lo, hi) in (bounds or {}).items():
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        outside = int((s.notna() & ~s.between(lo, hi)).sum())
        r.add(f"bounds[{col}]", outside == 0, f"{outside} of {n} outside [{lo}, {hi}]")

    for col, frac in (max_null_frac or {}).items():
        if col not in df.columns:
            continue
        got = float(df[col].isna().mean())
        r.add(
            f"nulls[{col}]", got <= frac, f"{got:.1%} null (ceiling {frac:.0%})", fatal=False
        )

    return r


def check_labels(labels: pd.DataFrame, subject: str, min_positives: int = 30) -> Report:
    """Label-specific gate: too few positives and spatial CV degenerates into
    noise -- better to stop here with advice than to publish a garbage map."""
    r = Report(subject)
    n = len(labels)
    r.add(
        "min_positive_cells",
        n >= min_positives,
        f"{n} labelled cells (need >= {min_positives}; widen the commodity filter, "
        "relax dev_stat, or coarsen the grid)",
    )
    return r
