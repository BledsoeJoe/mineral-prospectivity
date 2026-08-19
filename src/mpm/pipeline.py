"""Experiment orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from mpm.config import Config
from mpm.evaluate import aggregate, bias_audit, metrics, split_feature_groups
from mpm.pu import PUBagger
from mpm.splits import (
    buffered_train_mask,
    cell_latlng,
    fold_report,
    random_folds,
    spatial_block_folds,
)

log = logging.getLogger("mpm")


@dataclass
class RunResult:
    """Outcome of one cross-validated experiment."""

    name: str

    pooled: dict[str, float]
    """Metrics over every cell at once, each scored by a model that never saw
    it. This is the headline. With ~20 positives per fold, a per-fold mean of
    capture@5% is mostly sampling noise; pooling out-of-fold predictions before
    computing the metric is both more stable and closer to the question being
    asked ("rank this whole land package")."""

    fold_metrics: list[dict[str, float]]

    summary: dict[str, tuple[float, float]]
    """Per-fold mean and std. Read as a spread/stability estimate, not a point
    estimate."""
    scores: pd.DataFrame
    """One row per cell: h3, fold, score, score_std, calibrated, label."""
    importance: dict[str, float]
    c_hat: float

    def top(self, frac: float = 0.05) -> pd.DataFrame:
        """The highest-ranked ``frac`` of cells -- the drill-target shortlist."""
        k = max(1, round(frac * len(self.scores)))
        return self.scores.nlargest(k, "score")


def run_cv(
    df: pd.DataFrame,
    features: list[str],
    cfg: Config,
    name: str = "model",
    spatial: bool = True,
) -> RunResult:
    """Cross-validated PU run. Every cell is scored exactly once, by a model
    that never saw it or its buffered neighborhood."""
    cells = df[cfg.cell_col].to_numpy()
    y = df[cfg.label_col].to_numpy().astype(int)
    X = df[features].to_numpy(dtype=float)

    latlng = (
        df[["lat", "lon"]].to_numpy(dtype=float)
        if {"lat", "lon"} <= set(df.columns)
        else cell_latlng(cells)
    )

    folds = (
        spatial_block_folds(
            cells, y, cfg.split.n_folds, cfg.split.block_res, cfg.split.region_oversample, cfg.seed
        )
        if spatial
        else random_folds(len(df), cfg.split.n_folds, cfg.seed)
    )
    log.debug("[%s] fold sizes: %s", name, fold_report(folds, y, cfg.split.n_folds))

    score = np.full(len(df), np.nan)
    score_std = np.full(len(df), np.nan)
    calibrated = np.full(len(df), np.nan)
    fold_metrics: list[dict[str, float]] = []
    importances: list[dict[str, float]] = []
    c_hats: list[float] = []

    for f in range(cfg.split.n_folds):
        test = folds == f
        if not test.any():
            continue
        train = (
            buffered_train_mask(latlng, folds, f, cfg.split.buffer_km) if spatial else ~test
        )
        if y[train].sum() < 3:
            log.warning("fold %d: only %d training positives, skipping", f, int(y[train].sum()))
            continue

        model = PUBagger(
            n_bags=cfg.model.n_bags,
            neg_ratio=cfg.model.neg_ratio,
            informed_negatives=cfg.model.informed_negatives,
            informed_alpha=cfg.model.informed_alpha,
            lgbm_params=cfg.model.lgbm_params,
            seed=cfg.seed + f,
        ).fit(X[train], y[train], feature_names=features)

        pred = model.predict(X[test])
        score[test] = pred.score
        score_std[test] = pred.score_std
        calibrated[test] = pred.calibrated

        m = metrics(y[test], pred.score)
        m["fold"] = f
        m["train_cells"] = float(train.sum())
        m["train_positives"] = float(y[train].sum())
        fold_metrics.append(m)
        importances.append(model.feature_importance())
        c_hats.append(model.c_hat_)
        log.info(
            "fold %d | test %6d cells, %3d pos | capture@5%% %.3f | pr_auc %.4f",
            f, int(test.sum()), int(y[test].sum()), m.get("capture@5%", float("nan")), m.get("pr_auc", float("nan")),
        )

    scores = pd.DataFrame({
        cfg.cell_col: cells,
        "fold": folds,
        "score": score,
        "score_std": score_std,
        "calibrated": calibrated,
        cfg.label_col: y,
    })
    for extra in ("lat", "lon", "truth_deposit", "favorability"):
        if extra in df.columns:
            scores[extra] = df[extra].to_numpy()

    scored = ~np.isnan(score)
    pooled = metrics(y[scored], score[scored])
    pooled["cells_scored"] = float(scored.sum())

    mean_importance = (
        {k: float(np.mean([imp.get(k, 0.0) for imp in importances])) for k in features}
        if importances else {}
    )
    return RunResult(
        name=name,
        pooled=pooled,
        fold_metrics=fold_metrics,
        summary=aggregate(fold_metrics),
        scores=scores,
        importance=mean_importance,
        c_hat=float(np.mean(c_hats)) if c_hats else float("nan"),
    )


def run_experiment(df: pd.DataFrame, features: list[str], cfg: Config) -> dict[str, object]:
    """The full experiment: three models plus two audits.

    Models
      * ``geology``  -- geological/geophysical covariates only. The headline
        result, and the only one that should ever drive a targeting decision.
      * ``combined`` -- geology plus exploration-effort proxies. Scores better;
        means less.
      * ``naive_cv`` -- geology covariates under random k-fold. Included to put
        a number on how much optimism the standard protocol manufactures.

    Audits
      * bias audit: how much of ``combined``'s skill is effort, not geology.
      * discovery audit (synthetic cubes only): does the model rank the
        deposits that were never discovered? Nothing else in this pipeline can
        answer that.
    """
    geology, effort = split_feature_groups(features, cfg.effort_patterns)
    log.info("%d geology covariates, %d effort proxies", len(geology), len(effort))

    runs = {
        "geology": run_cv(df, geology, cfg, name="geology", spatial=True),
        "combined": run_cv(df, features, cfg, name="combined", spatial=True),
        "naive_cv": run_cv(df, geology, cfg, name="naive_cv", spatial=False),
    }

    audit = bias_audit(
        runs["geology"].pooled, runs["combined"].pooled, runs["combined"].importance, effort
    )
    optimism = {
        "spatial_cv_capture@5%": runs["geology"].pooled.get("capture@5%", np.nan),
        "random_cv_capture@5%": runs["naive_cv"].pooled.get("capture@5%", np.nan),
        "spatial_cv_roc_auc": runs["geology"].pooled.get("roc_auc", np.nan),
        "random_cv_roc_auc": runs["naive_cv"].pooled.get("roc_auc", np.nan),
    }
    optimism["capture_inflation"] = (
        optimism["random_cv_capture@5%"] - optimism["spatial_cv_capture@5%"]
    )
    optimism["roc_auc_inflation"] = optimism["random_cv_roc_auc"] - optimism["spatial_cv_roc_auc"]

    out: dict[str, object] = {
        "runs": runs, "bias_audit": audit, "optimism": optimism,
        "geology_features": geology, "effort_features": effort,
    }
    if "truth_deposit" in df.columns:
        out["discovery_audit"] = discovery_audit(runs["geology"], df, cfg)
    return out


def discovery_audit(run: RunResult, df: pd.DataFrame, cfg: Config) -> dict[str, float]:
    """Score the model against deposits it was never shown (synthetic only).

    ``capture@5%`` on known deposits is the number you can compute in the real
    world. ``undiscovered_capture@5%`` is the number you actually care about.
    The gap between them is the tax exploration bias charges you.
    """
    s = run.scores
    known = s[cfg.label_col].to_numpy().astype(int)
    truth = df["truth_deposit"].to_numpy().astype(int)
    hidden = ((truth == 1) & (known == 0)).astype(int)
    from mpm.evaluate import capture_at

    return {
        "n_true_deposits": float(truth.sum()),
        "n_discovered": float(known.sum()),
        "discovery_rate": float(known.sum() / max(truth.sum(), 1)),
        "known_capture@5%": capture_at(known, s["score"].to_numpy(), 0.05),
        "undiscovered_capture@5%": capture_at(hidden, s["score"].to_numpy(), 0.05),
        "all_true_capture@5%": capture_at(truth, s["score"].to_numpy(), 0.05),
    }
