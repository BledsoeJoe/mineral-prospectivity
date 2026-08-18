"""Figures. Three, each answering a question the numbers alone leave open."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from mpm.evaluate import capture_curve

PALETTE = {"spatial": "#1b6ca8", "random": "#c1440e", "known": "#111111", "hidden": "#e8a33d"}


def prospectivity_map(scores: pd.DataFrame, path: Path, title: str = "Prospectivity") -> None:
    """Score surface with known deposits overlaid, plus the ensemble-disagreement
    surface beside it.

    The uncertainty panel is the operationally useful one: a high score with
    high ensemble spread is a cell the model cannot make its mind up about,
    which is a very different proposition from a high score every bag agrees
    on. Ranking on the mean alone throws that distinction away.
    """
    if not {"lat", "lon"}.issubset(scores.columns):
        raise ValueError("scores frame needs lat/lon columns to plot")

    fig, axes = plt.subplots(1, 2, figsize=(15, 6), constrained_layout=True)
    s = scores.dropna(subset=["score"])

    for ax, col, cmap, label in (
        (axes[0], "score", "magma", "prospectivity score"),
        (axes[1], "score_std", "viridis", "ensemble std (disagreement)"),
    ):
        h = ax.scatter(s.lon, s.lat, c=s[col], s=3, cmap=cmap, linewidths=0)
        fig.colorbar(h, ax=ax, shrink=0.85, label=label)
        ax.set_xlabel("longitude")
        ax.set_aspect("equal")
    axes[0].set_ylabel("latitude")

    known = s[s.get("deposit", pd.Series(0, index=s.index)) == 1]
    axes[0].scatter(known.lon, known.lat, s=26, facecolors="none",
                    edgecolors="#00e5ff", linewidths=1.0, label=f"known deposits (n={len(known)})")

    if "truth_deposit" in s.columns:
        hidden = s[(s.truth_deposit == 1) & (s.deposit == 0)]
        axes[0].scatter(hidden.lon, hidden.lat, s=18, marker="x",
                        c=PALETTE["hidden"], linewidths=0.9,
                        label=f"undiscovered (n={len(hidden)})")
    axes[0].legend(loc="upper right", fontsize=8, framealpha=0.85)
    axes[0].set_title(title)
    axes[1].set_title("Where the ensemble disagrees")

    fig.savefig(path, dpi=150)
    plt.close(fig)


def capture_curves(runs: dict[str, pd.DataFrame], path: Path, label_col: str = "deposit") -> None:
    """Capture curves for each model, with the random-ranking baseline.

    Read it as: accept the x-axis fraction of land, capture the y-axis fraction
    of deposits. The gap between the spatial-CV and random-CV curves is the
    optimism that naive validation manufactures.
    """
    fig, ax = plt.subplots(figsize=(7, 6), constrained_layout=True)
    colors = {"geology": PALETTE["spatial"], "naive_cv": PALETTE["random"], "combined": "#3f8f4a"}

    for name, s in runs.items():
        s = s.dropna(subset=["score"])
        area, cap = capture_curve(s[label_col].to_numpy(), s["score"].to_numpy())
        at5 = np.interp(0.05, area, cap)
        ax.plot(area, cap, lw=2, color=colors.get(name), label=f"{name} (capture@5% = {at5:.2f})")

    ax.plot([0, 1], [0, 1], ls="--", c="grey", lw=1, label="random ranking")
    ax.axvline(0.05, ls=":", c="grey", lw=1)
    ax.set_xlabel("fraction of area accepted (H3 cells are equal-area)")
    ax.set_ylabel("fraction of known deposits captured")
    ax.set_title("Capture curves")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.25)

    fig.savefig(path, dpi=150)
    plt.close(fig)


def feature_importance(importance: dict[str, float], path: Path, top: int = 14) -> None:
    """Mean split-gain across bags. Collinear covariates share credit almost
    arbitrarily, so treat the ordering as indicative, not causal."""
    items = sorted(importance.items(), key=lambda kv: kv[1])[-top:]
    names = [k for k, _ in items]
    vals = [v for _, v in items]

    fig, ax = plt.subplots(figsize=(8, 0.42 * len(items) + 1.6), constrained_layout=True)
    ax.barh(names, vals, color=PALETTE["spatial"])
    ax.set_xlabel("mean split gain (normalised)")
    ax.set_title("Covariate importance")
    ax.grid(axis="x", alpha=0.25)

    fig.savefig(path, dpi=150)
    plt.close(fig)
