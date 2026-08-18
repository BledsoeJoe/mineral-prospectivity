"""Parse raw USGS files into tidy tables.

Everything in here is deliberately dumb about *meaning* (that lives in the
assemble stage) and careful about *format*, because the formats are hostile:

* MRDS ships as latin-1 CSV inside a zip, with free-text commodity fields and
  legacy rows located at Null Island.
* The geophysics grids are headerless whitespace-separated lon/lat/value
  triples.
* NGDB chemistry is a long-format table where a *negative* concentration means
  "below detection limit of abs(value)" -- a 1960s convention that will
  silently poison any statistic computed on the raw column.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("mpm.etl")

# ------------------------------------------------------------------ geophysics


def read_xyz_grid(path: Path, value_name: str) -> pd.DataFrame:
    """Read a headerless ``lon lat value`` grid (optionally gzipped).

    Returns columns ``lon, lat, <value_name>`` with non-finite rows dropped.
    """
    df = pd.read_csv(
        path,
        sep=r"\s+",
        header=None,
        names=["lon", "lat", value_name],
        dtype=float,
        compression="infer",
    )
    n0 = len(df)
    df = df.dropna()
    df = df[df["lat"].between(-90, 90) & df["lon"].between(-180, 180)]
    log.info("%s: %d grid nodes (%d dropped)", value_name, len(df), n0 - len(df))
    return df.reset_index(drop=True)


# ------------------------------------------------------------------------ MRDS

MRDS_USECOLS = [
    "dep_id", "site_name", "latitude", "longitude", "country", "state",
    "commod1", "commod2", "commod3", "dev_stat", "dep_type", "prod_size", "score",
]


def read_mrds(path: Path) -> pd.DataFrame:
    """MRDS occurrence records with coordinates and commodity fields.

    Keeps every development status -- prospects and occurrences are needed
    downstream both as candidate labels and as evidence of exploration effort.
    """
    with zipfile.ZipFile(path) as z, z.open("mrds.csv") as f:
        df = pd.read_csv(f, encoding="latin-1", usecols=MRDS_USECOLS, low_memory=False)
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    n0 = len(df)
    df = df.dropna(subset=["latitude", "longitude"])
    df = df[df["latitude"].between(-90, 90) & df["longitude"].between(-180, 180)]
    df = df[~((df["latitude"].abs() < 1e-6) & (df["longitude"].abs() < 1e-6))]  # Null Island
    log.info("mrds: %d records with usable coordinates (%d dropped)", len(df), n0 - len(df))
    return df.reset_index(drop=True)


def mrds_commodity_mask(df: pd.DataFrame, commodities: tuple[str, ...]) -> pd.Series:
    """Rows whose commodity fields mention any of ``commodities``.

    MRDS commodity fields are comma-joined free text ("Copper, Gold, Silver"),
    so this is substring matching by design; pass full commodity names.
    """
    joined = (
        df["commod1"].fillna("") + "," + df["commod2"].fillna("") + "," + df["commod3"].fillna("")
    ).str.lower()
    mask = pd.Series(False, index=df.index)
    for c in commodities:
        mask |= joined.str.contains(c.lower(), regex=False)
    return mask


# Development statuses that indicate an actual orebody rather than a hopeful
# stake in the ground. "Occurrence" and "Prospect" stay out of the label set:
# using them as positives teaches the model what optimism looks like.
MRDS_DEPOSIT_STATUSES = ("Producer", "Past Producer")


# ------------------------------------------------------------------- NGDB sed

NGDB_DEFAULT_ELEMENTS = ("Cu", "Zn", "Pb", "Ag", "Mo", "As", "Fe", "Mn")


def read_ngdb_sediment(
    path: Path,
    elements: tuple[str, ...] = NGDB_DEFAULT_ELEMENTS,
    chunksize: int = 2_000_000,
) -> pd.DataFrame:
    """Stream-sediment chemistry; see :func:`read_ngdb_geochem`."""
    return read_ngdb_geochem(path, elements=elements, chunksize=chunksize)


def _ngdb_member_prefix(z: zipfile.ZipFile) -> str:
    """NGDB zips nest their CSVs one directory deep (ngdbsed/, ngdbsoil/, ...);
    find that directory rather than hard-coding one media's name."""
    for info in z.infolist():
        if info.filename.endswith("/bestvalue.csv"):
            return info.filename.rsplit("/", 1)[0]
    raise ValueError(f"{z.filename}: no */bestvalue.csv member; not an NGDB csv zip")


def _strip_bom(col: str) -> str:
    """Some NGDB media (soil) ship with a UTF-8 BOM, others (sediment) do not.
    Under the latin-1 read the BOM decodes to 'ï»¿' glued to the first column
    name, so 'lab_id' silently stops existing. Same agency, same schema,
    different vintage of export tooling -- the usual."""
    return col.removeprefix("﻿").removeprefix("ï»¿")


def read_ngdb_geochem(
    path: Path,
    elements: tuple[str, ...] = NGDB_DEFAULT_ELEMENTS,
    chunksize: int = 2_000_000,
    column_prefix: str = "",
) -> pd.DataFrame:
    """NGDB geochemistry (any media: sediment, soil, concentrate), one row per
    sample, one column per element.

    ``column_prefix`` distinguishes media when several are in one cube:
    prefix ``soil_`` yields ``geochem_soil_cu_log_ppm`` alongside the
    sediment cube's ``geochem_cu_log_ppm``.

    The 'bestvalue' table is long format (sample x species) with two traps:

    * ``qvalue < 0`` means *censored*: the lab looked and saw less than
      abs(qvalue). Standard substitution: half the detection limit. Dropping
      censored rows would bias every district low exactly where sampling was
      most sensitive; keeping them raw would report negative concentrations.
    * units are a mix of ppm and pct; pct is converted (x 10,000) so a column
      is a column.

    Concentrations come back as log10(ppm): they are log-normal over four
    orders of magnitude, and a median of raw ppm is dominated by outliers.
    """
    with zipfile.ZipFile(path) as z:
        prefix = _ngdb_member_prefix(z)
        with z.open(f"{prefix}/main.csv") as f:
            sites = pd.read_csv(
                f,
                usecols=lambda c: _strip_bom(c) in {"lab_id", "lat_wgs84", "long_wgs84"},
                encoding="latin-1",
                low_memory=False,
            )
        sites.columns = [_strip_bom(c) for c in sites.columns]
        sites = sites.rename(columns={"lat_wgs84": "latitude", "long_wgs84": "longitude"})
        sites["latitude"] = pd.to_numeric(sites["latitude"], errors="coerce")
        sites["longitude"] = pd.to_numeric(sites["longitude"], errors="coerce")
        sites = sites.dropna().drop_duplicates(subset="lab_id")
        log.info("ngdb: %d sample sites", len(sites))

        wanted = set(elements)
        parts: list[pd.DataFrame] = []
        with z.open(f"{prefix}/bestvalue.csv") as f:
            for chunk in pd.read_csv(
                f,
                usecols=lambda c: _strip_bom(c) in {"lab_id", "species", "unit", "qvalue"},
                encoding="latin-1",
                chunksize=chunksize,
                low_memory=False,
            ):
                chunk.columns = [_strip_bom(c) for c in chunk.columns]
                part = chunk[chunk["species"].isin(wanted)].copy()
                if len(part):
                    parts.append(part)
        chem = pd.concat(parts, ignore_index=True)
    log.info("ngdb: %d element measurements for %s", len(chem), ",".join(elements))

    chem["qvalue"] = pd.to_numeric(chem["qvalue"], errors="coerce")
    chem = chem.dropna(subset=["qvalue"])
    chem = chem[chem["qvalue"] != 0]

    censored = chem["qvalue"] < 0
    chem.loc[censored, "qvalue"] = chem.loc[censored, "qvalue"].abs() / 2.0
    log.info("ngdb: %.1f%% of measurements censored, substituted DL/2", 100 * censored.mean())

    is_pct = chem["unit"].str.lower().eq("pct")
    chem.loc[is_pct, "qvalue"] *= 10_000.0

    chem["log_ppm"] = np.log10(chem["qvalue"])
    wide = (
        chem.pivot_table(index="lab_id", columns="species", values="log_ppm", aggfunc="median")
        .rename(columns={el: f"geochem_{column_prefix}{el.lower()}_log_ppm" for el in elements})
        .reset_index()
    )
    out = sites.merge(wide, on="lab_id", how="inner")
    log.info("ngdb: %d located samples with chemistry", len(out))
    return out
