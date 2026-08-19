"""Metrics that mean something to an exploration budget.

ROC-AUC is the default metric in the MPM literature and it is close to useless
here. With a 1-in-10,000 base rate, a model can score 0.95 AUC while its top
1% of ground contains almost no deposits, because AUC is dominated by how well
the model orders the vast barren majority against itself.

What a drilling program actually asks is: *if I can afford to work 5% of this
land package, what fraction of the endowment do I capture?* That is the
capture curve (called the prediction-rate curve in the MPM literature, after
Cheng & Agterberg 1999). It is reported here alongside PR-AUC, which is the
right summary statistic under extreme imbalance.

A convenient property of an H3 discrete global grid: cells are near
equal-area, so "top 5% of cells" is "top 5% of area" with no extra work.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

DEFAULT_THRESHOLDS: tuple[float, ...] = (0.01, 0.05, 0.10, 0.20)


def capture_curve(y: np.ndarray, score: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (area_fraction, captured_positive_fraction) over all cutoffs.

    Cells are ranked by score descending; the curve is the cumulative share of
    known deposits captured as you accept progressively more area.
    """
    y = np.asarray(y).astype(int)
    score = np.asarray(score, dtype=float)
    n = y.size
    if n == 0 or y.sum() == 0:
        return np.array([0.0, 1.0]), np.array([0.0, 0.0])

    order = np.argsort(-score, kind="mergesort")
    captured = np.cumsum(y[order]) / y.sum()
    area = np.arange(1, n + 1) / n
    return np.concatenate([[0.0], area]), np.concatenate([[0.0], captured])


def capture_at(y: np.ndarray, score: np.ndarray, frac: float) -> float:
    """Fraction of known deposits inside the top ``frac`` of ranked area."""
    area, captured = capture_curve(y, score)
    return float(np.interp(frac, area, captured))


def capture_auc(y: np.ndarray, score: np.ndarray) -> float:
    """Area under the capture curve. 0.5 == random ranking, 1.0 == perfect."""
    area, captured = capture_curve(y, score)
    return float(np.trapezoid(captured, area))


def metrics(
    y: np.ndarray,
    score: np.ndarray,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> dict[str, float]:
    """Full metric bundle for one held-out fold."""
    y = np.asarray(y).astype(int)
    score = np.asarray(score, dtype=float)
    out: dict[str, float] = {
        "n_cells": float(y.size),
        "n_positives": float(y.sum()),
        "base_rate": float(y.mean()) if y.size else float("nan"),
    }
    if y.sum() == 0 or y.sum() == y.size:
        # Undefined fold; caller should drop it rather than average in a nan.
        out.update({"roc_auc": float("nan"), "pr_auc": float("nan"), "capture_auc": float("nan")})
        for t in thresholds:
            out[f"capture@{t:.0%}"] = float("nan")
            out[f"lift@{t:.0%}"] = float("nan")
        return out

    out["roc_auc"] = float(roc_auc_score(y, score))
    out["pr_auc"] = float(average_precision_score(y, score))
    out["capture_auc"] = capture_auc(y, score)
    for t in thresholds:
        cap = capture_at(y, score, t)
        out[f"capture@{t:.0%}"] = cap
        out[f"lift@{t:.0%}"] = cap / t
    return out


def aggregate(fold_metrics: list[dict[str, float]]) -> dict[str, tuple[float, float]]:
    """Mean and std of each metric across folds, ignoring undefined folds."""
    keys = sorted({k for m in fold_metrics for k in m})
    out: dict[str, tuple[float, float]] = {}
    for k in keys:
        vals = np.array([m.get(k, np.nan) for m in fold_metrics], dtype=float)
        vals = vals[~np.isnan(vals)]
        out[k] = (float(vals.mean()), float(vals.std())) if vals.size else (float("nan"), float("nan"))
    return out


def split_feature_groups(
    columns: list[str], effort_patterns: tuple[str, ...]
) -> tuple[list[str], list[str]]:
    """Partition covariates into (geology, effort) by substring match."""
    effort = [c for c in columns if any(p in c.lower() for p in effort_patterns)]
    geology = [c for c in columns if c not in set(effort)]
    return geology, effort


def bias_audit(
    geology_metrics: dict[str, float],
    combined_metrics: dict[str, float],
    combined_importance: dict[str, float],
    effort_features: list[str],
    key: str = "capture@5%",
) -> dict[str, float]:
    """Quantify how much of the model's apparent skill comes from knowing where
    people have already looked.

    A model that scores well only once accessibility proxies are added is not
    finding deposits, it is finding exploration history -- and it will send you
    to ground that has already been drilled out. A large ``effort_gain`` paired
    with a large ``effort_importance_share`` is the signature of that failure.
    """
    geo = float(geology_metrics.get(key, float("nan")))
    comb = float(combined_metrics.get(key, float("nan")))
    share = sum(combined_importance.get(f, 0.0) for f in effort_features)
    return {
        f"geology_only_{key}": geo,
        f"with_effort_{key}": comb,
        "effort_gain": comb - geo,
        "effort_importance_share": float(share),
    }
