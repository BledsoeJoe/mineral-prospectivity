"""Spatial cross-validation.

Random k-fold CV on a geospatial grid is broken. Neighbouring cells share
covariate values (geophysical grids are smooth, geology polygons span many
cells), so a random split puts near-duplicates of every test cell in the
training set. The model memorises location, the ROC-AUC comes out at 0.98, and
the map is worthless on ground you have not already drilled.

This module implements the standard fix: group cells into spatial blocks, hold
out whole contiguous *regions* of blocks, and additionally drop training cells
that sit within a buffer distance of the held-out region.

Reference points: Roberts et al. (2017), "Cross-validation strategies for data
with temporal, spatial, hierarchical, or phylogenetic structure"; Valavi et al.
(2019), blockCV.
"""

from __future__ import annotations

import h3
import numpy as np
from sklearn.cluster import KMeans
from sklearn.neighbors import BallTree

EARTH_RADIUS_KM = 6371.0088


def cell_latlng(cells: np.ndarray) -> np.ndarray:
    """(n, 2) array of cell centroids in degrees.

    This is the one unavoidable Python-level loop over every cell, so callers
    should compute it once and pass it around rather than recomputing per fold.
    """
    return np.array([h3.cell_to_latlng(c) for c in cells], dtype=float)


def cells_to_blocks(cells: np.ndarray, block_res: int) -> np.ndarray:
    """Map each H3 cell to its coarse-resolution parent (the CV block id)."""
    return np.array([h3.cell_to_parent(c, block_res) for c in cells], dtype=object)


def block_centroids(blocks: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unique block ids and their (lat, lon) centroids in degrees."""
    uniq = np.unique(blocks)
    coords = np.array([h3.cell_to_latlng(b) for b in uniq], dtype=float)
    return uniq, coords


def _to_unit_sphere(latlon_deg: np.ndarray) -> np.ndarray:
    """Lat/lon degrees -> 3D unit vectors, so clustering has no wrap-around or
    high-latitude distortion."""
    lat = np.radians(latlon_deg[:, 0])
    lon = np.radians(latlon_deg[:, 1])
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


def haversine_km(a_latlon: np.ndarray, b_latlon: np.ndarray) -> np.ndarray:
    """Dense pairwise great-circle distance matrix in km. Shapes (n,2), (m,2).

    Only for small inputs; :func:`nearest_distance_km` is the scalable path.
    """
    lat1 = np.radians(a_latlon[:, 0])[:, None]
    lon1 = np.radians(a_latlon[:, 1])[:, None]
    lat2 = np.radians(b_latlon[:, 0])[None, :]
    lon2 = np.radians(b_latlon[:, 1])[None, :]
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def nearest_distance_km(query_latlon: np.ndarray, target_latlon: np.ndarray) -> np.ndarray:
    """Distance from each query point to its nearest target point, in km.

    Uses a haversine BallTree so this stays tractable on cubes with millions of
    cells, where a dense distance matrix would not fit in memory.
    """
    if len(target_latlon) == 0:
        return np.full(len(query_latlon), np.inf)
    tree = BallTree(np.radians(target_latlon), metric="haversine")
    dist, _ = tree.query(np.radians(query_latlon), k=1)
    return dist.ravel() * EARTH_RADIUS_KM


def spatial_block_folds(
    cells: np.ndarray,
    y: np.ndarray,
    n_folds: int,
    block_res: int,
    region_oversample: int = 20,
    seed: int = 0,
) -> np.ndarray:
    """Assign every cell to a CV fold made of contiguous spatial regions.

    Blocks are over-clustered into ``n_folds * region_oversample`` contiguous
    regions, then regions are greedily binned into folds by longest-processing-
    time: regions are placed heaviest-first, each into the fold that minimises
    a *joint* cost over positive count and cell count, both normalised to their
    per-fold targets.

    Balancing both dimensions matters. Balance on positives alone and every
    zero-positive region lands in the same bin, leaving one fold with 80% of
    the map. Balance on area alone and a fold can end up with no deposits at
    all and no computable metric. Deposits cluster into districts, so with
    coarse regions neither is achievable; ``region_oversample`` is the lever
    that makes the packing feasible.

    Returns an integer array of fold ids, one per cell.
    """
    blocks = cells_to_blocks(cells, block_res)
    uniq_blocks, centroids = block_centroids(blocks)

    n_regions = min(len(uniq_blocks), n_folds * region_oversample)
    km = KMeans(n_clusters=n_regions, random_state=seed, n_init=10)
    region_of_block = km.fit_predict(_to_unit_sphere(centroids))

    block_to_region = dict(zip(uniq_blocks, region_of_block))
    region_of_cell = np.array([block_to_region[b] for b in blocks], dtype=int)

    pos_per_region = np.bincount(region_of_cell[y == 1], minlength=n_regions).astype(float)
    n_per_region = np.bincount(region_of_cell, minlength=n_regions).astype(float)

    pos_scale = max(pos_per_region.sum(), 1.0) / n_folds
    n_scale = max(n_per_region.sum(), 1.0) / n_folds

    # Longest-processing-time order: place the most awkward regions while there
    # is still slack to absorb them. "Heaviest" = whichever of the two
    # normalised loads is larger.
    order = np.argsort(-np.maximum(pos_per_region / pos_scale, n_per_region / n_scale))

    fold_pos = np.zeros(n_folds)
    fold_n = np.zeros(n_folds)
    region_to_fold = np.zeros(n_regions, dtype=int)

    for r in order:
        cost = (fold_pos + pos_per_region[r]) / pos_scale + (fold_n + n_per_region[r]) / n_scale
        f = int(np.argmin(cost))
        region_to_fold[r] = f
        fold_pos[f] += pos_per_region[r]
        fold_n[f] += n_per_region[r]

    return region_to_fold[region_of_cell]


def random_folds(n: int, n_folds: int, seed: int = 0) -> np.ndarray:
    """Plain random k-fold. Provided only so the pipeline can quantify how much
    optimism naive CV buys you -- do not use it to report a result."""
    rng = np.random.default_rng(seed)
    return rng.permutation(np.arange(n) % n_folds)


def buffered_train_mask(
    latlng: np.ndarray,
    folds: np.ndarray,
    test_fold: int,
    buffer_km: float,
) -> np.ndarray:
    """Boolean mask over cells: True == usable for training this fold.

    Excludes the held-out fold, plus every training cell within ``buffer_km``
    of any held-out cell. The buffer is computed cell-to-cell against a
    haversine BallTree, so it is an exact geographic buffer rather than a
    block-quantised approximation: the distance you configure is the distance
    you get.
    """
    is_test = folds == test_fold
    mask = ~is_test
    if buffer_km <= 0 or not is_test.any() or not mask.any():
        return mask

    train_idx = np.flatnonzero(mask)
    dist = nearest_distance_km(latlng[train_idx], latlng[is_test])
    mask[train_idx[dist < buffer_km]] = False
    return mask


def fold_report(folds: np.ndarray, y: np.ndarray, n_folds: int) -> list[dict[str, float]]:
    """Per-fold cell and positive counts, for logging split quality."""
    return [
        {"fold": f, "cells": float((folds == f).sum()), "positives": float(y[folds == f].sum())}
        for f in range(n_folds)
    ]
