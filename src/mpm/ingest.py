"""Ingest and clean a mineral prospectivity datacube.

Two entry points:

* :func:`clean_cube` -- takes an already-gridded table (one row per H3 cell,
  covariates in columns) such as the Lawley et al. (2022) datacube, and makes
  it model-ready.
* :func:`points_to_cells` -- takes a raw deposit/occurrence point file (MRDS,
  USMIN, ARDF, or a national survey export) and turns it into per-cell labels.

The point-file path is where most of the real work is. Mineral occurrence
databases are compilations of compilations: the same orebody appears under
three names with coordinates that disagree by a kilometre, commodity fields are
free text, and "occurrence" covers everything from a producing mine to a
roadside stain of malachite. Every one of those is a labelling decision that
moves the model more than the choice of classifier does.
"""

from __future__ import annotations

import re

import h3
import pandas as pd

# --------------------------------------------------------------- point files


def points_to_cells(
    points: pd.DataFrame,
    resolution: int,
    lat_col: str = "latitude",
    lon_col: str = "longitude",
    commodity_col: str | None = None,
    commodities: tuple[str, ...] = (),
    name_col: str | None = None,
) -> pd.DataFrame:
    """Index deposit points to H3 cells, filtering by commodity and deduping.

    Deduplication is intentionally two-stage:

    1. **By name within a cell.** The same deposit listed twice in one cell is
       one deposit. Names are normalised (case, punctuation, mine/deposit
       suffixes) before comparison.
    2. **By cell.** Two genuinely distinct orebodies in the same 5 km cell
       still produce one positive label, because the cell is the unit of
       prediction. Collapsing them is correct; counting them twice would
       silently upweight well-endowed districts.

    Returns a frame with ``h3`` and ``n_occurrences`` per cell.
    """
    df = points.copy()
    df = df.dropna(subset=[lat_col, lon_col])
    df = df[df[lat_col].between(-90, 90) & df[lon_col].between(-180, 180)]
    # Null Island and (0, lon) rows are near-universal in legacy compilations.
    df = df[~((df[lat_col].abs() < 1e-6) & (df[lon_col].abs() < 1e-6))]

    if commodity_col and commodities:
        pattern = "|".join(re.escape(c) for c in commodities)
        df = df[df[commodity_col].astype(str).str.contains(pattern, case=False, na=False)]

    if df.empty:
        return pd.DataFrame({"h3": pd.Series(dtype=str), "n_occurrences": pd.Series(dtype=int)})

    df["h3"] = [
        h3.latlng_to_cell(float(a), float(b), resolution)
        for a, b in zip(df[lat_col], df[lon_col])
    ]

    if name_col and name_col in df.columns:
        df["_key"] = df[name_col].astype(str).map(normalise_name)
        df = df.drop_duplicates(subset=["h3", "_key"])

    return (
        df.groupby("h3", as_index=False)
        .size()
        .rename(columns={"size": "n_occurrences"})
    )


_SUFFIXES = re.compile(
    r"\b(mine|mines|deposit|prospect|occurrence|claim|group|shaft|pit|no\.?\s*\d+)\b"
)


def normalise_name(name: str) -> str:
    """Fold a deposit name to a comparison key."""
    s = name.lower().strip()
    s = _SUFFIXES.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def attach_labels(
    cube: pd.DataFrame,
    labelled_cells: pd.DataFrame,
    cell_col: str = "h3",
    label_col: str = "deposit",
) -> pd.DataFrame:
    """Left-join point-derived labels onto the covariate cube."""
    out = cube.merge(labelled_cells[[cell_col]].assign(**{label_col: 1}), on=cell_col, how="left")
    out[label_col] = out[label_col].fillna(0).astype(int)
    return out


# ---------------------------------------------------------------- cube clean


def clean_cube(
    df: pd.DataFrame,
    cell_col: str = "h3",
    label_col: str = "deposit",
    drop_cols: tuple[str, ...] = (),
    max_missing_frac: float = 0.5,
    max_categories: int = 12,
) -> tuple[pd.DataFrame, list[str]]:
    """Return (clean frame, covariate column names).

    Steps, in order:

    * drop duplicate cells (keeping the first) and rows with no cell id;
    * drop columns that are constant, or missing in more than
      ``max_missing_frac`` of cells -- a covariate with 80% coverage encodes
      *survey extent*, which is exploration effort wearing a lab coat;
    * one-hot encode low-cardinality categoricals (lithology, province) and
      drop high-cardinality ones (map-unit ids are effectively row ids);
    * median-impute the remainder and add an explicit ``<col>_isna`` indicator
      wherever anything was missing, so "unsurveyed" stays visible to the model
      instead of being disguised as an average value.
    """
    df = df.dropna(subset=[cell_col]).drop_duplicates(subset=[cell_col]).reset_index(drop=True)

    reserved = set(drop_cols) | {cell_col, label_col}
    candidates = [c for c in df.columns if c not in reserved]

    keep_numeric: list[str] = []
    onehot_frames: list[pd.DataFrame] = []

    for col in candidates:
        s = df[col]
        if s.isna().mean() > max_missing_frac:
            continue
        if pd.api.types.is_numeric_dtype(s):
            if s.nunique(dropna=True) > 1:
                keep_numeric.append(col)
        else:
            n_levels = s.nunique(dropna=True)
            if 1 < n_levels <= max_categories:
                dummies = pd.get_dummies(s.astype("string"), prefix=col, dtype=float)
                onehot_frames.append(dummies)

    out = df[[cell_col, label_col] + [c for c in ("lat", "lon") if c in df.columns]].copy()
    for c in ("truth_deposit", "detect_p", "favourability"):
        if c in df.columns:
            out[c] = df[c]

    numeric = df[keep_numeric].astype(float)
    na_flags = {
        f"{c}_isna": numeric[c].isna().astype(float)
        for c in keep_numeric
        if numeric[c].isna().any()
    }
    numeric = numeric.fillna(numeric.median(numeric_only=True))

    blocks = [out, numeric] + ([pd.DataFrame(na_flags)] if na_flags else []) + onehot_frames
    clean = pd.concat(blocks, axis=1)

    features = [c for c in clean.columns if c not in reserved and c not in {"lat", "lon"}]
    features = [c for c in features if c not in {"truth_deposit", "detect_p", "favourability"}]
    return clean, features


def summarise(df: pd.DataFrame, label_col: str = "deposit") -> dict[str, float]:
    """Quick shape/imbalance summary for logging."""
    n = len(df)
    pos = int(df[label_col].sum())
    return {
        "cells": n,
        "positives": pos,
        "base_rate": pos / n if n else float("nan"),
        "cells_per_positive": n / pos if pos else float("inf"),
    }
