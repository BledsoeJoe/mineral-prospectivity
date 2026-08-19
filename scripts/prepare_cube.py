#!/usr/bin/env python
"""Turn a real, downloaded dataset into the parquet cube this pipeline expects.

Two supported shapes.

1. ``gridded`` -- an already-H3-indexed covariate table, e.g. the Lawley et al.
   (2021) datacube released as NRCan Open File 8836. One row per H3 cell,
   covariates in columns, a column marking deposit presence.

2. ``points``  -- a raw occurrence file (USGS MRDS, USMIN, ARDF, a state survey
   export) plus a separate covariate grid. The points are indexed to H3,
   deduplicated, and joined on.

Where to get the data (all open, all verified landing pages -- the file layout
inside each release changes, so check column names before trusting the
defaults below):

  Lawley et al. datacube, H3-indexed covariates + labels for US/Canada/Australia
    NRCan Open File 8836   https://doi.org/10.4095/329203
    mirror                 https://ostrnrcan-dostrncan.canada.ca/handle/1845/134061
    the paper              https://doi.org/10.1016/j.oregeorev.2021.104635

  USGS ScienceBase companions to the same study
    gridded geology        https://www.sciencebase.gov/catalog/item/623a013ed34e915b67cddcfa
    published model grids  https://data.usgs.gov/datacatalog/data/USGS:619552fbd34eb622f6906b26

  Raw occurrence databases
    USGS MRDS              https://mrdata.usgs.gov/mrds/
    USGS SGMC geology      https://doi.org/10.3133/ds1052
    sediment-hosted Zn-Pb  https://pubs.usgs.gov/of/2009/1252/

Usage
-----
    python scripts/prepare_cube.py gridded \\
        --covariates ~/data/lawley_datacube.csv \\
        --h3-col H3_Address --label-col Training_MVT_Deposit \\
        --out data/mvt_cube.parquet

    python scripts/prepare_cube.py points \\
        --covariates ~/data/covariates.csv --h3-col h3 \\
        --points ~/data/mrds.csv --commodities Zinc Lead \\
        --out data/cube.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mpm.ingest import attach_labels, points_to_cells, summarise
from mpm.splits import cell_latlng

H3_CANDIDATES = ("h3", "h3_address", "h3address", "h3_index", "hex_id", "hexid", "h3_id")


def read_any(path: Path) -> pd.DataFrame:
    """Read csv / tsv / parquet by extension."""
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".tsv", ".tab"}:
        return pd.read_csv(path, sep="\t", low_memory=False)
    return pd.read_csv(path, low_memory=False)


def detect_h3_column(df: pd.DataFrame, override: str | None) -> str:
    """Find the H3 index column, or fail loudly with the available names."""
    if override:
        if override not in df.columns:
            raise SystemExit(f"--h3-col {override!r} not in file. Columns: {list(df.columns)[:40]}")
        return override
    lowered = {c.lower(): c for c in df.columns}
    for cand in H3_CANDIDATES:
        if cand in lowered:
            return lowered[cand]
    raise SystemExit(
        "Could not find an H3 column. Pass --h3-col explicitly.\n"
        f"Columns present: {list(df.columns)[:40]}"
    )


def add_centroids(df: pd.DataFrame, h3_col: str) -> pd.DataFrame:
    """Attach lat/lon centroids so the pipeline can buffer folds without
    recomputing them on every fold."""
    latlng = cell_latlng(df[h3_col].to_numpy())
    df["lat"] = latlng[:, 0]
    df["lon"] = latlng[:, 1]
    return df


def finalise(df: pd.DataFrame, h3_col: str, out: Path) -> None:
    df = df.rename(columns={h3_col: "h3"})
    df = add_centroids(df, "h3")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"wrote {out}")
    print(f"cube: {summarise(df)}")
    if df["deposit"].sum() < 20:
        print(
            "\nWARNING: fewer than 20 positive cells. Spatial CV will be extremely "
            "noisy. Consider a coarser H3 resolution, a wider commodity filter, or "
            "including occurrences alongside deposits."
        )


def cmd_gridded(args: argparse.Namespace) -> None:
    df = read_any(Path(args.covariates))
    h3_col = detect_h3_column(df, args.h3_col)

    if args.label_col not in df.columns:
        raise SystemExit(
            f"--label-col {args.label_col!r} not found. Columns: {list(df.columns)[:60]}"
        )
    # Published cubes encode labels variously as 0/1, True/False, or "Present".
    raw = df[args.label_col]
    if raw.dtype == object:
        label = raw.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "present", "y"})
    else:
        label = raw.fillna(0).astype(float) > 0
    df = df.drop(columns=[args.label_col])
    df["deposit"] = label.astype(int)

    finalise(df, h3_col, Path(args.out))


def cmd_points(args: argparse.Namespace) -> None:
    cov = read_any(Path(args.covariates))
    h3_col = detect_h3_column(cov, args.h3_col)
    resolution = _resolution_of(cov[h3_col].iloc[0])
    print(f"covariate grid is H3 resolution {resolution}")

    pts = read_any(Path(args.points))
    labelled = points_to_cells(
        pts,
        resolution=resolution,
        lat_col=args.lat_col,
        lon_col=args.lon_col,
        commodity_col=args.commodity_col,
        commodities=tuple(args.commodities or ()),
        name_col=args.name_col,
    )
    print(f"{len(pts)} raw points -> {len(labelled)} labelled cells after filter + dedupe")

    cov = cov.rename(columns={h3_col: "h3"})
    finalise(attach_labels(cov, labelled), "h3", Path(args.out))


def _resolution_of(cell: str) -> int:
    import h3

    return h3.get_resolution(cell)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gridded", help="covariates and labels already in one H3 table")
    g.add_argument("--covariates", required=True)
    g.add_argument("--h3-col", default=None)
    g.add_argument("--label-col", required=True)
    g.add_argument("--out", default="data/cube.parquet")
    g.set_defaults(func=cmd_gridded)

    q = sub.add_parser("points", help="H3 covariate grid + a separate occurrence point file")
    q.add_argument("--covariates", required=True)
    q.add_argument("--h3-col", default=None)
    q.add_argument("--points", required=True)
    q.add_argument("--lat-col", default="latitude")
    q.add_argument("--lon-col", default="longitude")
    q.add_argument("--commodity-col", default="commod1")
    q.add_argument("--commodities", nargs="*", default=[])
    q.add_argument("--name-col", default="site_name")
    q.add_argument("--out", default="data/cube.parquet")
    q.set_defaults(func=cmd_points)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
