"""Positive-unlabeled learning for mineral prospectivity.

The label problem: we have ~10^2 known deposits and ~10^6 cells. The remaining
cells are **unlabeled**, not negative. A cell with no recorded deposit might be
barren, or might be a deposit under 400 m of cover that nobody has drilled. If
you call all of them negatives and train a standard classifier, you teach the
model that undiscovered deposits look like background -- which is precisely the
class of cell you are trying to find.

Two mitigations are implemented here:

1. **PU bagging** (Mordelet & Vert, 2014). Train an ensemble; each member sees
   all positives plus a small bootstrap of unlabeled cells cast as negatives.
   Any given unlabeled cell is a "negative" in only some members, so a hidden
   positive is contradicted rather than burned in. The ensemble spread is also
   a free, honest uncertainty estimate.

2. **Informed negative sampling.** A first-pass model scores the unlabeled
   pool; the second pass draws its pseudo-negatives preferentially from cells
   the first pass considers unlike the positives. This is the tabular analogue
   of the undersampling scheme in GFM4MPM (Mohammadi et al., 2024).

Also implemented: the Elkan & Noto (2008) constant ``c = P(labeled | y=1)``,
which rescales the "labeled vs unlabeled" score into an estimate of the true
posterior. Note that Elkan-Noto assumes SCAR (labeled positives are Selected
Completely At Random). That assumption is *false* in exploration -- discovery
depends on outcrop, road access and historic land tenure -- so ``c`` here is a
diagnostic, not a guarantee. Quantifying that violation is what
``mpm.evaluate.bias_audit`` is for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from lightgbm import LGBMClassifier


@dataclass
class PUResult:
    """Scores and diagnostics from a fitted PU ensemble."""

    score: np.ndarray
    """Mean ensemble score in [0, 1]. Rank-meaningful; not calibrated."""

    score_std: np.ndarray
    """Std-dev across bags. High values mark cells the ensemble disagrees on."""

    calibrated: np.ndarray
    """Elkan-Noto rescaled score = score / c, clipped to [0, 1]."""

    c_hat: float
    """Estimated P(labeled | y=1) under SCAR. Low values imply most positives
    in the region are still undiscovered."""


class PUBagger:
    """Bagged positive-unlabeled classifier over tabular covariates."""

    def __init__(
        self,
        n_bags: int = 25,
        neg_ratio: float = 1.0,
        informed_negatives: bool = True,
        informed_alpha: float = 2.0,
        pos_holdout: float = 0.25,
        lgbm_params: dict | None = None,
        seed: int = 0,
    ) -> None:
        self.n_bags = n_bags
        self.neg_ratio = neg_ratio
        self.informed_negatives = informed_negatives
        self.informed_alpha = informed_alpha
        self.pos_holdout = pos_holdout
        self.lgbm_params = dict(lgbm_params or {})
        self.seed = seed
        self.models_: list[tuple[LGBMClassifier, np.ndarray, np.ndarray]] = []
        self.c_hat_: float = 1.0
        self.feature_names_: list[str] | None = None

    # ------------------------------------------------------------------ fit

    def fit(self, X: np.ndarray, s: np.ndarray, feature_names: list[str] | None = None) -> PUBagger:
        """Fit on covariates ``X`` and the *label indicator* ``s`` (1 = known
        deposit, 0 = unlabeled). ``s`` is deliberately not called ``y``."""
        X = np.asarray(X, dtype=np.float64)
        s = np.asarray(s).astype(int)
        self.feature_names_ = feature_names

        pos_idx = np.flatnonzero(s == 1)
        unl_idx = np.flatnonzero(s == 0)
        if pos_idx.size == 0:
            raise ValueError("no positive labels in training split")
        if unl_idx.size == 0:
            raise ValueError("no unlabeled cells in training split")

        weights = np.ones(unl_idx.size)
        if self.informed_negatives and self.informed_alpha > 0:
            first_pass = self._run_bags(X, pos_idx, unl_idx, weights, n_bags=max(5, self.n_bags // 3))
            prelim = self._aggregate_oob(X, first_pass, unl_idx)
            # Prefer pseudo-negatives the first pass is confident about.
            weights = np.clip(1.0 - prelim, 1e-6, 1.0) ** self.informed_alpha

        self.models_ = self._run_bags(X, pos_idx, unl_idx, weights, n_bags=self.n_bags)
        self.c_hat_ = self._estimate_c(X, pos_idx)
        return self

    def _run_bags(
        self,
        X: np.ndarray,
        pos_idx: np.ndarray,
        unl_idx: np.ndarray,
        weights: np.ndarray,
        n_bags: int,
    ) -> list[tuple[LGBMClassifier, np.ndarray]]:
        """Train ``n_bags`` members.

        Each bag also holds out a slice of the positives (``pos_holdout``) so
        that :meth:`_estimate_c` has out-of-sample positives to work with. A bag
        trained on every positive will recognise every positive, which is how
        you get a meaningless c_hat of 0.99.

        Returns (model, in-bag unlabeled idx, held-out positive idx) triples.
        """
        rng = np.random.default_rng(self.seed)
        n_draw = min(unl_idx.size, max(1, round(self.neg_ratio * pos_idx.size)))
        n_hold = round(self.pos_holdout * pos_idx.size)
        n_hold = min(max(n_hold, 1), max(pos_idx.size - 2, 1)) if pos_idx.size > 3 else 0
        p = weights / weights.sum()

        out: list[tuple[LGBMClassifier, np.ndarray, np.ndarray]] = []
        for b in range(n_bags):
            perm = rng.permutation(pos_idx)
            held, fit_pos = (perm[:n_hold], perm[n_hold:]) if n_hold else (perm[:0], perm)
            drawn = rng.choice(unl_idx, size=n_draw, replace=False, p=p)
            idx = np.concatenate([fit_pos, drawn])
            y_bag = np.concatenate([np.ones(fit_pos.size), np.zeros(drawn.size)])
            model = LGBMClassifier(random_state=self.seed + b, **self.lgbm_params)
            model.fit(X[idx], y_bag)
            out.append((model, drawn, held))
        return out

    @staticmethod
    def _aggregate_oob(
        X: np.ndarray,
        bags: list[tuple[LGBMClassifier, np.ndarray, np.ndarray]],
        unl_idx: np.ndarray,
    ) -> np.ndarray:
        """Out-of-bag score for each unlabeled cell: averaged only over the
        members that did *not* train on it. Falls back to the full-ensemble mean
        for cells that happened to be in every bag."""
        total = np.zeros(unl_idx.size)
        count = np.zeros(unl_idx.size)
        position = {int(v): i for i, v in enumerate(unl_idx)}
        preds = np.column_stack([m.predict_proba(X[unl_idx])[:, 1] for m, _, _ in bags])

        for j, (_, drawn, _) in enumerate(bags):
            oob = np.ones(unl_idx.size, dtype=bool)
            oob[[position[int(d)] for d in drawn]] = False
            total[oob] += preds[oob, j]
            count[oob] += 1

        out = np.where(count > 0, total / np.maximum(count, 1), preds.mean(axis=1))
        return out

    def _estimate_c(self, X: np.ndarray, pos_idx: np.ndarray) -> float:
        """Elkan & Noto (2008) constant: c = E[g(x) | x is a labeled positive],
        averaged over the positives each bag held out.

        Caveat worth stating plainly: each bag is trained on a class-rebalanced
        sample, so g is not an estimate of P(s=1|x) on the natural base rate and
        this c_hat is *not* the discovery rate. Read it as a held-out positive
        recognition rate -- a diagnostic for whether the ensemble generalises
        across positives, not a calibration guarantee. Elkan-Noto also assumes
        SCAR, which exploration data violates by construction.
        """
        vals = [
            float(m.predict_proba(X[held])[:, 1].mean())
            for m, _, held in self.models_
            if held.size
        ]
        if not vals:
            vals = [
                float(m.predict_proba(X[pos_idx])[:, 1].mean()) for m, _, _ in self.models_
            ]
        return float(np.clip(np.mean(vals), 1e-3, 1.0))

    # -------------------------------------------------------------- predict

    def predict(self, X: np.ndarray) -> PUResult:
        """Score unseen cells. Use on the held-out fold only."""
        if not self.models_:
            raise RuntimeError("call fit() before predict()")
        X = np.asarray(X, dtype=np.float64)
        preds = np.column_stack([m.predict_proba(X)[:, 1] for m, _, _ in self.models_])
        mean = preds.mean(axis=1)
        return PUResult(
            score=mean,
            score_std=preds.std(axis=1),
            calibrated=np.clip(mean / self.c_hat_, 0.0, 1.0),
            c_hat=self.c_hat_,
        )

    def feature_importance(self) -> dict[str, float]:
        """Mean split-gain importance across bags, normalised to sum to 1."""
        gains = np.mean([m.booster_.feature_importance("gain") for m, _, _ in self.models_], axis=0)
        total = gains.sum() or 1.0
        names = self.feature_names_ or [f"f{i}" for i in range(len(gains))]
        return dict(zip(names, gains / total))
