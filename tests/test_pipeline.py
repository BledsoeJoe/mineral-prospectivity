"""Tests for the pieces where a silent bug would produce a plausible-looking
but wrong map: fold leakage, capture-curve arithmetic, and label dedup."""

from __future__ import annotations

import h3
import numpy as np
import pandas as pd
import pytest

from mpm.config import Config
from mpm.evaluate import capture_at, capture_auc, capture_curve, metrics, split_feature_groups
from mpm.ingest import clean_cube, normalize_name, points_to_cells
from mpm.pu import PUBagger
from mpm.splits import (
    buffered_train_mask,
    cell_latlng,
    cells_to_blocks,
    haversine_km,
    nearest_distance_km,
    spatial_block_folds,
)
from mpm.synth import make_synthetic_cube


@pytest.fixture(scope="module")
def cube() -> pd.DataFrame:
    return make_synthetic_cube(resolution=5, n_true_deposits=150, seed=3)


# ------------------------------------------------------------------- splits


def test_haversine_matches_known_distance():
    # London Heathrow -> JFK, ~5555 km great circle.
    a = np.array([[51.47, -0.4543]])
    b = np.array([[40.6413, -73.7781]])
    assert haversine_km(a, b)[0, 0] == pytest.approx(5555, rel=0.01)


def test_blocks_are_h3_parents(cube):
    cells = cube.h3.to_numpy()[:50]
    blocks = cells_to_blocks(cells, 3)
    assert all(h3.get_resolution(b) == 3 for b in blocks)
    assert all(h3.cell_to_parent(c, 3) == b for c, b in zip(cells, blocks))


def test_folds_cover_every_cell_exactly_once(cube):
    folds = spatial_block_folds(cube.h3.to_numpy(), cube.deposit.to_numpy(), 5, 3, seed=0)
    assert folds.shape == (len(cube),)
    assert set(np.unique(folds)).issubset(set(range(5)))


def test_folds_respect_block_boundaries(cube):
    """A CV block must never be split across folds -- that is the leak."""
    cells = cube.h3.to_numpy()
    folds = spatial_block_folds(cells, cube.deposit.to_numpy(), 5, 3, seed=0)
    blocks = cells_to_blocks(cells, 3)
    per_block = pd.DataFrame({"b": blocks, "f": folds}).groupby("b").f.nunique()
    assert per_block.max() == 1


def test_folds_are_balanced_in_area_and_positives(cube):
    """Greedy binning on positives alone starves a fold of area. Guard it."""
    y = cube.deposit.to_numpy()
    folds = spatial_block_folds(cube.h3.to_numpy(), y, 5, 3, seed=0)
    cells_per_fold = np.bincount(folds, minlength=5)
    assert cells_per_fold.min() > 0.25 * cells_per_fold.mean()
    assert cells_per_fold.max() < 3.0 * cells_per_fold.mean()
    assert (np.bincount(folds[y == 1], minlength=5) > 0).sum() >= 4


def test_buffer_excludes_neighbors_of_test_fold(cube):
    cells = cube.h3.to_numpy()
    latlng = cell_latlng(cells)
    folds = spatial_block_folds(cells, cube.deposit.to_numpy(), 5, 3, seed=0)
    unbuffered = buffered_train_mask(latlng, folds, 0, buffer_km=0.0)
    buffered = buffered_train_mask(latlng, folds, 0, buffer_km=60.0)

    assert not buffered[folds == 0].any(), "test cells leaked into training"
    assert buffered.sum() < unbuffered.sum(), "buffer removed nothing"
    assert buffered.sum() > 0, "buffer removed everything"
    # Buffering may only ever remove cells, never add them.
    assert np.all(unbuffered[buffered])


def test_buffer_distance_is_honoured_exactly(cube):
    """Every surviving training cell must genuinely be beyond the buffer."""
    cells = cube.h3.to_numpy()
    latlng = cell_latlng(cells)
    folds = spatial_block_folds(cells, cube.deposit.to_numpy(), 5, 3, seed=0)
    mask = buffered_train_mask(latlng, folds, 0, buffer_km=60.0)
    d = nearest_distance_km(latlng[mask], latlng[folds == 0])
    assert d.min() >= 60.0


# ------------------------------------------------------------------ metrics


def test_capture_curve_endpoints_and_monotonicity():
    y = np.array([1, 0, 0, 1, 0, 0, 0, 0, 1, 0])
    score = np.linspace(1, 0, 10)
    area, cap = capture_curve(y, score)
    assert cap[0] == 0.0 and cap[-1] == pytest.approx(1.0)
    assert np.all(np.diff(cap) >= -1e-12)
    assert np.all(np.diff(area) >= 0)


def test_perfect_and_random_rankings_bracket_capture_auc():
    rng = np.random.default_rng(0)
    y = (rng.random(2000) < 0.02).astype(int)
    perfect = y.astype(float) + rng.random(2000) * 1e-6
    assert capture_auc(y, perfect) > 0.98
    assert capture_auc(y, rng.random(2000)) == pytest.approx(0.5, abs=0.08)


def test_capture_at_is_a_fraction_of_positives():
    """Top 1% of 1000 cells == 10 cells. Rank 5 of the 10 positives into that
    window and bury the rest; capture@1% must be 0.5, not 1.0."""
    y = np.zeros(1000, dtype=int)
    y[:10] = 1
    score = np.zeros(1000)
    score[:5] = 1.0            # five positives at the top
    score[5:10] = -1.0         # the other five at the very bottom
    score[10:] = np.linspace(0.9, 0.0, 990)
    assert capture_at(y, score, 0.01) == pytest.approx(0.5, abs=0.01)


def test_metrics_returns_nan_for_degenerate_fold():
    m = metrics(np.zeros(50, dtype=int), np.random.default_rng(0).random(50))
    assert np.isnan(m["pr_auc"]) and np.isnan(m["capture@5%"])


def test_effort_features_are_quarantined_by_name():
    cols = ["sed_thickness_km", "dist_to_road_km", "pop_density", "moho_depth_km", "sample_density"]
    geology, effort = split_feature_groups(cols, Config().effort_patterns)
    assert set(effort) == {"dist_to_road_km", "pop_density", "sample_density"}
    assert set(geology) == {"sed_thickness_km", "moho_depth_km"}


# ----------------------------------------------------------------------- PU


def test_pu_bagger_separates_a_learnable_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(3000, 5))
    latent = X[:, 0] + X[:, 1] > 2.0
    s = np.zeros(3000, dtype=int)
    labeled = np.flatnonzero(latent)[:40]  # only some positives are labeled
    s[labeled] = 1

    model = PUBagger(n_bags=10, seed=0, lgbm_params={"n_estimators": 60, "verbose": -1}).fit(X, s)
    pred = model.predict(X)
    assert pred.score[latent].mean() > pred.score[~latent].mean() + 0.2
    assert 0.0 < model.c_hat_ <= 1.0
    assert pred.score_std.shape == pred.score.shape


def test_pu_bagger_rejects_missing_classes():
    X = np.random.default_rng(0).normal(size=(20, 3))
    with pytest.raises(ValueError):
        PUBagger(n_bags=2).fit(X, np.zeros(20, dtype=int))


def test_feature_importance_is_normalized():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(600, 4))
    s = (X[:, 0] > 1.6).astype(int)
    model = PUBagger(n_bags=5, seed=0, lgbm_params={"n_estimators": 40, "verbose": -1}).fit(
        X, s, feature_names=["a", "b", "c", "d"]
    )
    imp = model.feature_importance()
    assert set(imp) == {"a", "b", "c", "d"}
    assert sum(imp.values()) == pytest.approx(1.0)


# ------------------------------------------------------------------- ingest


def test_normalize_name_folds_deposit_suffixes():
    assert normalize_name("Red Dog Mine") == normalize_name("RED-DOG deposit")
    assert normalize_name("Buick No. 2 Shaft") == "buick"


def test_points_to_cells_filters_dedupes_and_drops_null_island():
    pts = pd.DataFrame({
        "latitude": [36.5, 36.5001, 36.5, 0.0, 200.0, 40.0],
        "longitude": [-93.2, -93.2001, -93.2, 0.0, 10.0, -95.0],
        "commod": ["Zinc, Lead", "Zn", "Zinc", "Zinc", "Zinc", "Gold"],
        "name": ["Foo Mine", "Foo deposit", "Bar prospect", "X", "Y", "Z"],
    })
    out = points_to_cells(
        pts, resolution=7, commodity_col="commod", commodities=("zinc", "zn"), name_col="name"
    )
    assert len(out) == 1, "nearby duplicates should collapse into one cell"
    assert int(out.n_occurrences.iloc[0]) == 2, "Foo dedupes, Bar survives"


def test_clean_cube_drops_leakage_columns_and_sparse_covariates(cube):
    df = cube.copy()
    df["mostly_missing"] = np.nan
    df.loc[df.index[:5], "mostly_missing"] = 1.0
    df["constant"] = 7.0
    df["lithology"] = np.where(df.carbonate_frac > 0.5, "carbonate", "clastic")

    clean, features = clean_cube(df, drop_cols=Config().drop_cols)
    assert "truth_deposit" not in features and "favorability" not in features
    assert "lat" not in features and "lon" not in features
    assert "mostly_missing" not in features and "constant" not in features
    assert "lithology_carbonate" in features
    assert not clean[features].isna().any().any()
    assert clean.h3.is_unique


def test_clean_cube_flags_imputed_values():
    df = pd.DataFrame({
        "h3": [h3.latlng_to_cell(35 + i * 0.5, -95, 6) for i in range(20)],
        "deposit": [0] * 19 + [1],
        "gravity": [np.nan] * 4 + list(range(16)),
    })
    _, features = clean_cube(df)
    assert "gravity_isna" in features, "imputation must stay visible to the model"


# ----------------------------------------------------------------- synthetic


def test_synthetic_cube_is_a_biased_pu_problem(cube):
    assert cube.deposit.sum() < cube.truth_deposit.sum(), "detection must lose deposits"
    assert (cube.deposit == 1).any()
    # Discovered deposits should sit in more accessible ground than undiscovered.
    found = cube[cube.deposit == 1].dist_to_road_km.mean()
    missed = cube[(cube.truth_deposit == 1) & (cube.deposit == 0)].dist_to_road_km.mean()
    assert found < missed, "the bias engine is not producing biased discovery"


def test_synthetic_deposits_track_latent_favorability(cube):
    assert cube[cube.truth_deposit == 1].favorability.mean() > cube.favorability.mean() + 0.1
