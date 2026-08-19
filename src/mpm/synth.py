"""A synthetic H3 datacube with exploration bias built in.

Why bother, when real data is one download away? Because on real data you can
never measure the thing that matters. You can only score a model against
*known* deposits, which are exactly the ones that were easy to find. If your
model is secretly a road-proximity detector, known-deposit validation will
applaud it.

Here the generator knows three things the model does not:

* ``favorability`` -- the latent geological driver,
* ``truth_deposit`` -- every deposit that exists, discovered or not,
* ``detect_p``      -- the probability each one was ever recorded.

Deposits are recorded only where a biased detection process found them
(accessible, outcropping, historically sampled ground). So you can ask the
question the real world refuses to answer: *does a model trained on the biased
sample still rank the undiscovered deposits highly?* That is the number this
whole project exists to produce.

The geology here is a caricature loosely shaped after sediment-hosted Zn-Pb
systems -- carbonate host, thick basin fill, cratonic-edge fluid pathway. It is
not a substitute for the real datacube; it is a test harness for the pipeline
and a demonstration of the failure mode.
"""

from __future__ import annotations

import h3
import numpy as np
import pandas as pd


def _random_fourier_field(
    xy: np.ndarray, n_components: int, length_scale_km: float, rng: np.random.Generator
) -> np.ndarray:
    """A smooth, stationary random field: sum of random plane waves.

    ``length_scale_km`` sets the spatial autocorrelation range -- the reason
    random k-fold CV leaks in the first place.
    """
    freqs = rng.normal(0, 1.0 / length_scale_km, size=(n_components, 2))
    phases = rng.uniform(0, 2 * np.pi, size=n_components)
    amps = rng.normal(0, 1.0, size=n_components)
    field = (np.cos(xy @ freqs.T + phases) * amps).sum(axis=1)
    return (field - field.mean()) / (field.std() + 1e-9)


def _orthogonalise(v: np.ndarray, basis: list[np.ndarray]) -> np.ndarray:
    """Residual of ``v`` after projecting out ``basis``, renormalized.

    Needed because two "independent" smooth random fields over a domain only a
    few correlation lengths wide are not remotely independent -- with ~16
    effective degrees of freedom, sample correlations of 0.6+ arise routinely.
    Left alone, a decoy covariate correlated with the true driver outranks it,
    and the demo teaches the wrong lesson. Orthogonalising guarantees the decoys
    carry no *linear* information about the drivers.

    (That correlation is itself real: long-wavelength geophysical covariates
    over a single basin genuinely are collinear, which is why feature-importance
    rankings in published prospectivity models deserve more skepticism than they
    usually get. Here it is removed so the test harness stays interpretable.)
    """
    out = v - v.mean()
    for b in basis:
        b0 = b - b.mean()
        denom = float(b0 @ b0)
        if denom > 1e-12:
            out = out - (out @ b0) / denom * b0
    return out / (out.std() + 1e-9)


def make_synthetic_cube(
    resolution: int = 6,
    bbox: tuple[float, float, float, float] = (32.0, -104.0, 41.0, -92.0),
    n_true_deposits: int = 400,
    seed: int = 17,
) -> pd.DataFrame:
    """Build a labeled synthetic datacube.

    ``bbox`` is (lat_min, lon_min, lat_max, lon_max); the default covers a
    chunk of the US midcontinent purely so the coordinates look plausible.

    Returns one row per H3 cell with covariates, the observable label
    ``deposit``, and the hidden ground-truth columns.
    """
    rng = np.random.default_rng(seed)
    lat0, lon0, lat1, lon1 = bbox

    poly = h3.LatLngPoly([(lat0, lon0), (lat0, lon1), (lat1, lon1), (lat1, lon0)])
    cells = sorted(h3.h3shape_to_cells(poly, resolution))
    latlng = np.array([h3.cell_to_latlng(c) for c in cells], dtype=float)
    lat, lon = latlng[:, 0], latlng[:, 1]

    # Project to an approximate local km plane so length scales are meaningful.
    xy = np.column_stack([
        (lon - lon.mean()) * 111.32 * np.cos(np.radians(lat.mean())),
        (lat - lat.mean()) * 110.57,
    ])
    field = lambda ls: _random_fourier_field(xy, 60, ls, rng)

    # ---------------------------------------------------------- geology layers
    # NOTE: these are deliberately correlated with one another, as real
    # potential-field and lithospheric covariates are.
    sed_thickness_km = np.clip(3.0 + 2.0 * field(220.0), 0.05, None)
    carbonate_frac = 1 / (1 + np.exp(-1.6 * field(160.0)))
    craton_field = field(300.0)
    dist_craton_edge_km = np.abs(craton_field) * 140.0
    heatflow_mwm2 = 55.0 + 12.0 * field(260.0)
    mag_longwave_nt = 40.0 * field(190.0)
    grav_gradient_mgal = 8.0 * np.abs(np.gradient(craton_field)) + 2.0 * field(120.0)
    moho_depth_km = 38.0 + 5.0 * field(340.0)

    # ------------------------------------------------- latent favorability
    # Deliberately non-additive: a basin only works if it is thick enough to
    # drive brine, carbonate enough to trap, and close enough to a crustal
    # discontinuity to plumb. Additive models should visibly underperform.
    thickness_window = np.exp(-((sed_thickness_km - 4.0) ** 2) / (2 * 1.1**2))
    edge_proximity = np.exp(-dist_craton_edge_km / 60.0)
    thermal = 1 / (1 + np.exp(0.25 * (heatflow_mwm2 - 62.0)))  # cool basins favoured
    favorability = thickness_window * (0.35 + 0.65 * carbonate_frac) * edge_proximity * thermal
    favorability = (favorability - favorability.min()) / np.ptp(favorability)

    # ------------------------------------------------------- true occurrences
    weights = favorability**3
    p_true = weights / weights.sum() * n_true_deposits
    truth_deposit = (rng.random(len(cells)) < np.clip(p_true, 0, 1)).astype(int)

    # ------------------------------------ discovery process (the bias engine)
    dist_to_road_km = np.clip(30.0 * np.exp(field(150.0)) - 5.0, 0.2, None)
    pop_density = np.clip(np.exp(2.0 + 1.2 * field(130.0)), 0.1, None)
    outcrop_frac = np.clip(1 / (1 + np.exp(-1.4 * field(180.0))), 0.01, 0.99)
    sample_density = np.clip(
        np.exp(1.5 * outcrop_frac + 0.6 * np.log(pop_density) - 0.03 * dist_to_road_km), 0.05, None
    )

    detect_logit = (
        -1.1
        + 2.4 * outcrop_frac
        - 0.045 * dist_to_road_km
        + 0.45 * np.log1p(sample_density)
    )
    detect_p = 1 / (1 + np.exp(-detect_logit))
    deposit = (truth_deposit == 1) & (rng.random(len(cells)) < detect_p)
    deposit = deposit.astype(int)

    # ---------------------- covariates as *measured*: latent fields plus noise
    noise = lambda scale: rng.normal(0, scale, size=len(cells))

    drivers = [sed_thickness_km, carbonate_frac, dist_craton_edge_km, heatflow_mwm2, favorability]
    decoy_smooth = _orthogonalise(field(200.0), drivers)
    decoy_rough = _orthogonalise(field(40.0), drivers)

    df = pd.DataFrame({
        "h3": cells,
        "lat": lat,
        "lon": lon,
        # geology / geophysics
        "sed_thickness_km": sed_thickness_km + noise(0.25),
        "carbonate_frac": np.clip(carbonate_frac + noise(0.04), 0, 1),
        "dist_craton_edge_km": dist_craton_edge_km + noise(8.0),
        "heatflow_mwm2": heatflow_mwm2 + noise(2.0),
        "mag_longwave_nt": mag_longwave_nt + noise(4.0),
        "grav_gradient_mgal": grav_gradient_mgal + noise(0.6),
        "moho_depth_km": moho_depth_km + noise(0.8),
        # decoys: real cubes are full of covariates with no causal role
        "decoy_smooth": decoy_smooth,
        "decoy_rough": decoy_rough,
        "decoy_white": noise(1.0),
        # exploration-effort proxies (quarantined by name, see config.py)
        "dist_to_road_km": dist_to_road_km,
        "pop_density": pop_density,
        "outcrop_frac": outcrop_frac,
        "sample_density": sample_density,
        # observable label
        "deposit": deposit,
        # hidden ground truth -- dropped before modeling, used only to score
        "truth_deposit": truth_deposit,
        "detect_p": detect_p,
        "favorability": favorability,
    })

    df["dist_to_deposit_km"] = _distance_to_nearest(latlng, deposit == 1)
    return df


def _distance_to_nearest(latlng: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Great-circle distance from every cell to the nearest flagged cell.

    Included as a deliberate leakage canary: under random CV this covariate
    looks like the best predictor in the cube, because deposits cluster. Under
    spatial CV it collapses. Watching it collapse is the point.
    """
    from mpm.splits import haversine_km

    if not mask.any():
        return np.full(len(latlng), np.nan)
    d = haversine_km(latlng, latlng[mask])
    np.putmask(d, d <= 0, np.inf)  # ignore self-distance for labeled cells
    out = d.min(axis=1)
    return np.where(np.isfinite(out), out, 0.0)
