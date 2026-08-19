"""Configuration objects for the prospectivity pipeline.

Everything that changes an experimental result lives here, so a run is
reproducible from (config, seed, input parquet) alone.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Feature-name substrings that mark a covariate as a proxy for *where people
# have looked* rather than *where rocks are favorable*. Anything matching these
# is quarantined into the "effort" group and excluded from the honest model.
# See docs in README: exploration bias is the central threat to validity in MPM.
DEFAULT_EFFORT_PATTERNS: tuple[str, ...] = (
    "road",
    "rail",
    "pop",
    "urban",
    "nightlight",
    "accessib",
    "drillhole",
    "drill_density",
    "sample_density",
    "mapping",
    "claim",
    "outcrop",
    "cover_thickness",
    "exposure",
    "effort",
    "dist_to_mine",
    "dist_to_deposit",
    "_isna",
)
# `_isna` deserves a note: clean_cube adds missingness indicators so that
# "unsurveyed" stays visible to the model. But *which cells a survey covered*
# is exploration effort by definition -- a geochem gap means nobody sampled
# there, not that the rocks are different. The indicators stay in the cube for
# the audit model; the headline model must not see them.


@dataclass(frozen=True)
class ModelConfig:
    """Base learner + PU-bagging hyperparameters."""

    n_bags: int = 25
    """Number of PU bags. Each bag = all positives + a fresh unlabeled draw."""

    neg_ratio: float = 1.0
    """Unlabeled cells drawn per bag, as a multiple of the positive count."""

    informed_negatives: bool = True
    """Two-stage sampling: bias the unlabeled draw away from cells that a
    first-pass model already scores as deposit-like (they may be undiscovered
    positives). See Mordelet & Vert (2014); Xiong & Zuo (2020)."""

    informed_alpha: float = 2.0
    """Sharpness of the informed-negative reweighting. 0 == uniform sampling."""

    lgbm_params: dict[str, Any] = field(
        default_factory=lambda: {
            "n_estimators": 300,
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_child_samples": 5,
            "subsample": 0.8,
            "subsample_freq": 1,
            "colsample_bytree": 0.7,
            "reg_lambda": 1.0,
            "verbose": -1,
        }
    )


@dataclass(frozen=True)
class SplitConfig:
    """Spatial cross-validation settings."""

    n_folds: int = 5

    block_res: int = 3
    """H3 resolution used to aggregate cells into CV blocks. Coarser (smaller
    number) == larger blocks == harder, more honest evaluation. At res 3 a block
    averages ~12,400 km2; the modeling cells at res 7 average ~5.2 km2."""

    region_oversample: int = 20
    """Cluster blocks into n_folds * region_oversample contiguous regions before
    binning them into folds. Higher values give the packer finer granularity and
    therefore better-balanced folds, at the cost of each held-out region being
    smaller. That trade is acceptable here because train/test separation is
    enforced by `buffer_km` rather than by region size; at high oversample a
    region converges to a single block and this becomes textbook blockCV."""

    buffer_km: float = 50.0
    """Training cells whose block centroid falls within this distance of a test
    block centroid are dropped. Kills the halo of spatially autocorrelated
    training data that sits just across the fold boundary."""


@dataclass(frozen=True)
class Config:
    """Top-level experiment config."""

    cell_col: str = "h3"
    label_col: str = "deposit"

    drop_cols: tuple[str, ...] = ("lat", "lon", "truth_deposit", "detect_p", "favorability")
    """Columns present in the cube that must never be used as covariates.
    `truth_deposit` etc. only exist in the synthetic cube (they are the hidden
    ground truth) and would be catastrophic leakage."""

    effort_patterns: tuple[str, ...] = DEFAULT_EFFORT_PATTERNS

    max_missing_frac: float = 0.5
    """Drop covariates missing in more than this fraction of cells."""

    seed: int = 17

    model: ModelConfig = field(default_factory=ModelConfig)
    split: SplitConfig = field(default_factory=SplitConfig)

    # ---------------------------------------------------------------- loaders

    @classmethod
    def from_file(cls, path: str | Path) -> Config:
        path = Path(path)
        raw = (
            yaml.safe_load(path.read_text())
            if path.suffix in {".yaml", ".yml"}
            else json.loads(path.read_text())
        ) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Config:
        raw = dict(raw)
        model = ModelConfig(**raw.pop("model", {}))
        split = SplitConfig(**raw.pop("split", {}))
        for key in ("drop_cols", "effort_patterns"):
            if key in raw:
                raw[key] = tuple(raw[key])
        return cls(model=model, split=split, **raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
