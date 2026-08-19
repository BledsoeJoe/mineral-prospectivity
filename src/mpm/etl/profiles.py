"""Named end-to-end pipeline configurations.

A profile is everything the pipeline needs to go from nothing to a published
run: which sources, which region, which commodity defines a positive, which
grid resolution, and any overrides to the modeling config. Profiles are data,
not code -- adding a nickel cube is a new entry here, not a new pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    kind: str  # "synth" | "usgs"

    # -- usgs profiles ------------------------------------------------------
    h3_res: int = 5
    """Cell size. Res 5 (~250 km^2) keeps the conterminous US near 30k cells:
    big enough for spatial CV to mean something, small enough that a full
    pipeline run is minutes, not hours."""

    bbox: tuple[float, float, float, float] = (-125.0, 24.0, -66.0, 49.5)
    """lon_min, lat_min, lon_max, lat_max. Default: conterminous US, which is
    what the geophysics grids cover."""

    commodities: tuple[str, ...] = ("Copper",)
    producer_only: bool = True
    """Positives are Producer/Past Producer sites only. Occurrences and
    prospects are *not* negatives and not positives either -- they rejoin the
    unlabeled pool, which is exactly what PU learning is for."""

    prod_sizes: tuple[str, ...] = ()
    """Optional MRDS prod_size filter (L/M/S/Y/N/U). ("L", "M") restricts
    positives to deposits that sustained real production -- without it, "small
    producer" sweeps in thousands of one-adit historical workings and the label
    stops meaning *orebody*."""

    include_geochem: bool = True
    """The NGDB download is 165 MB and its parse is the slowest reader; off
    switches to a pure geophysics cube for quick runs."""

    # -- synth profiles -----------------------------------------------------
    synth_resolution: int = 6
    synth_n_deposits: int = 400
    seed: int = 17

    # -- modeling ----------------------------------------------------------
    config_overrides: dict[str, Any] = field(default_factory=dict)
    """Merged over mpm.config.Config defaults for the train stage."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PROFILES: dict[str, Profile] = {
    "synth": Profile(
        name="synth",
        description=(
            "Synthetic cube with hidden ground truth. No network needed; the only "
            "profile where undiscovered-deposit capture is measurable."
        ),
        kind="synth",
    ),
    "us-cu": Profile(
        name="us-cu",
        description=(
            "Conterminous-US copper cube built from scratch: MRDS labels + "
            "aeromagnetics + gravity + stream-sediment geochemistry."
        ),
        kind="usgs",
        commodities=("Copper",),
        prod_sizes=("L", "M"),
        # Res-5 cells are ~7x the area of the synthetic res-6 cube's cells, so
        # the same res-3 CV blocks hold fewer cells each; a slightly wider
        # buffer compensates for the coarser grid's longer correlation range.
        # max_missing_frac is raised past the geochem coverage gap (52% of
        # cells have no stream-sediment sample): the values stay, imputed where
        # absent, and the _isna indicators carry "unsampled" into the effort
        # group instead of the covariate being silently dropped.
        config_overrides={
            "split": {"block_res": 3, "buffer_km": 75.0},
            "max_missing_frac": 0.6,
        },
    ),
    "us-znpb": Profile(
        name="us-znpb",
        description="Conterminous-US zinc-lead cube; same sources as us-cu.",
        kind="usgs",
        commodities=("Zinc", "Lead"),
        prod_sizes=("L", "M"),
        config_overrides={
            "split": {"block_res": 3, "buffer_km": 75.0},
            "max_missing_frac": 0.6,
        },
    ),
}
