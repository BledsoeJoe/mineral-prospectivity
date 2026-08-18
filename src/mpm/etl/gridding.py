"""Aggregate point and grid-node data onto the H3 cell grid.

H3 is the common frame everything joins on: near-equal-area cells, a clean
parent/child hierarchy the spatial CV blocks reuse, and a single string key
that makes every join a groupby instead of a geometry operation.
"""

from __future__ import annotations

import logging

import h3
import numpy as np
import pandas as pd

log = logging.getLogger("mpm.etl")


def assign_h3(df: pd.DataFrame, res: int, lat_col: str = "lat", lon_col: str = "lon") -> pd.Series:
    """H3 cell for each row. The python h3 bindings are scalar, so this is a
    plain loop -- ~2M points/min, paid once per raw file and then cached by the
    stage manifest."""
    lats = df[lat_col].to_numpy(dtype=float)
    lons = df[lon_col].to_numpy(dtype=float)
    to_cell = h3.latlng_to_cell
    cells = [to_cell(lat, lon, res) for lat, lon in zip(lats, lons)]
    return pd.Series(cells, index=df.index, name="h3")


def bbox_filter(
    df: pd.DataFrame, bbox: tuple[float, float, float, float],
    lat_col: str = "lat", lon_col: str = "lon",
) -> pd.DataFrame:
    """Keep rows inside (lon_min, lat_min, lon_max, lat_max)."""
    lon_min, lat_min, lon_max, lat_max = bbox
    out = df[df[lat_col].between(lat_min, lat_max) & df[lon_col].between(lon_min, lon_max)]
    return out.reset_index(drop=True)


def grid_to_cells(
    df: pd.DataFrame,
    res: int,
    value_col: str,
    lat_col: str = "lat",
    lon_col: str = "lon",
) -> pd.DataFrame:
    """Aggregate a dense grid (or point cloud) to per-cell mean and roughness.

    ``<value>_mean`` carries the anomaly itself; ``<value>_std`` is the
    within-cell spread -- a cheap texture/gradient proxy that geophysicists
    read as structural complexity, and tree models find genuinely useful.
    """
    work = df[[lat_col, lon_col, value_col]].copy()
    work["h3"] = assign_h3(work, res, lat_col, lon_col)
    agg = work.groupby("h3")[value_col].agg(["mean", "std", "count"])
    out = pd.DataFrame({
        "h3": agg.index,
        f"{value_col}_mean": agg["mean"].to_numpy(),
        f"{value_col}_std": agg["std"].fillna(0.0).to_numpy(),
    })
    log.info("%s: %d nodes -> %d cells", value_col, len(work), len(out))
    return out


def points_to_cell_stats(
    df: pd.DataFrame,
    res: int,
    value_cols: list[str],
    lat_col: str = "latitude",
    lon_col: str = "longitude",
    count_name: str | None = None,
) -> pd.DataFrame:
    """Aggregate sparse point samples to per-cell medians (+ optional count).

    Median, not mean: geochemical point data has fat tails, and one hot sample
    should move a cell, not own it. The sample count is returned under
    ``count_name`` when asked for -- callers name it so that effort-flavoured
    counts (sampling density) get names the quarantine list recognises.
    """
    work = df.copy()
    work["h3"] = assign_h3(work, res, lat_col, lon_col)
    agg = work.groupby("h3")[value_cols].median()
    out = agg.reset_index()
    if count_name:
        out[count_name] = work.groupby("h3").size().to_numpy(dtype=float)
    log.info("points: %d samples -> %d cells", len(work), len(out))
    return out


def cell_centroids(cells: pd.Series) -> pd.DataFrame:
    lat, lon = zip(*(h3.cell_to_latlng(c) for c in cells))
    return pd.DataFrame({"h3": cells.to_numpy(), "lat": np.array(lat), "lon": np.array(lon)})
