"""Registry of upstream datasets.

Every source is open USGS data with a stable direct-download URL, verified
programmatically fetchable. Checksums are *recorded*, not pinned: USGS revises
these compilations in place, so the pipeline fingerprints whatever it actually
downloaded and lets downstream stages recompute when the content changes.
Pin ``sha256`` on a source if you need a run frozen to an exact vintage.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    filename: str
    description: str
    citation: str
    license: str = "Public domain (USGS)"
    sha256: str | None = None
    """Optional pin. None == accept whatever upstream serves, record its hash."""


MRDS = Source(
    name="mrds",
    url="https://mrdata.usgs.gov/mrds/mrds-csv.zip",
    filename="mrds-csv.zip",
    description=(
        "USGS Mineral Resources Data System: ~300k mineral occurrence, prospect "
        "and mine records. Labels come from here; so do effort proxies, because "
        "MRDS is as much a map of where people have looked as of what they found."
    ),
    citation="USGS Mineral Resources Data System, https://mrdata.usgs.gov/mrds/",
)

MAGNETIC = Source(
    name="magnetic",
    url="https://mrdata.usgs.gov/magnetic/magnetic.xyz.gz",
    filename="magnetic.xyz.gz",
    description=(
        "Merged aeromagnetic anomaly grid of the conterminous US (~1 km), as "
        "lon/lat/nT triples. Magnetic anomalies image intrusions and basement "
        "structure under cover -- a genuine formation-side covariate."
    ),
    citation="Bankey et al. (2002), USGS Open-File Report 02-361",
)

GRAVITY_ISOSTATIC = Source(
    name="gravity_isostatic",
    url="https://mrdata.usgs.gov/gravity/isostatic/isograv.xyz.gz",
    filename="isograv.xyz.gz",
    description=(
        "Isostatic residual gravity anomaly grid of the conterminous US, "
        "lon/lat/mGal. Residual gravity highs flag dense intrusive complexes "
        "and basement uplifts; lows flag basins."
    ),
    citation="Phillips et al. (1993), USGS Digital Data Series DDS-9",
)

GRAVITY_BOUGUER = Source(
    name="gravity_bouguer",
    url="https://mrdata.usgs.gov/gravity/bouguer/bouguer.xyz.gz",
    filename="bouguer.xyz.gz",
    description="Complete Bouguer gravity anomaly grid of the conterminous US, lon/lat/mGal.",
    citation="Phillips et al. (1993), USGS Digital Data Series DDS-9",
)

NGDB_SEDIMENT = Source(
    name="ngdb_sediment",
    url="https://mrdata.usgs.gov/ngdb/sediment/ngdbsed-csv.zip",
    filename="ngdbsed-csv.zip",
    description=(
        "National Geochemical Database, stream-sediment samples: ~400k sites "
        "with multi-element chemistry. Long-format 'best value' table with "
        "censored values encoded as negative detection limits -- the cleaning "
        "in readers.py exists because of this file."
    ),
    citation="USGS National Geochemical Database, https://mrdata.usgs.gov/ngdb/sediment/",
)

REGISTRY: dict[str, Source] = {
    s.name: s for s in (MRDS, MAGNETIC, GRAVITY_ISOSTATIC, GRAVITY_BOUGUER, NGDB_SEDIMENT)
}


# Curated, verified additions for the catalog: each of these has been
# downloaded and parsed end-to-end against the current readers, so the
# "register a source" UI can offer them with a straight face. Kept separate
# from REGISTRY: these are opt-in, not part of every cube.
SUGGESTED_SOURCES: tuple[dict, ...] = (
    {
        "name": "ngdb_soil",
        "url": "https://mrdata.usgs.gov/ngdb/soil/ngdbsoil-csv.zip",
        "kind": "ngdb_geochem",
        "value_name": "soil",
        "size_mb": 61,
        "description": (
            "NGDB soil geochemistry: same schema as the stream-sediment media, "
            "different sampling medium. Soil samples respond to bedrock through "
            "residual cover where drainages are sparse."
        ),
        "citation": "USGS National Geochemical Database, https://mrdata.usgs.gov/ngdb/soil/",
    },
    {
        "name": "ngdb_concentrate",
        "url": "https://mrdata.usgs.gov/ngdb/concentrate/ngdbconc-csv.zip",
        "kind": "ngdb_geochem",
        "value_name": "conc",
        "size_mb": 55,
        "description": (
            "NGDB heavy-mineral concentrates: panned stream-sediment fractions. "
            "Concentrating the dense minerals amplifies ore-element signals that "
            "whole-sediment chemistry dilutes away."
        ),
        "citation": "USGS National Geochemical Database, https://mrdata.usgs.gov/ngdb/concentrate/",
    },
)
