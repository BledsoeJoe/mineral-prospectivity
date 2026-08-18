import React from 'react'

function Paper({ title, authors, venue, href, children }) {
  return (
    <div className="paper">
      <div className="p-title"><a href={href} target="_blank" rel="noreferrer">{title}</a></div>
      <div className="p-venue">{authors} · {venue}</div>
      <div className="p-how">{children}</div>
    </div>
  )
}

export default function Research() {
  return (
    <div className="prose-page">
      <div className="prose">
        <h1>Research</h1>
        <p className="lede">
          Every idea in this project is borrowed. These are the papers it
          borrows from, with links, and exactly where each one lands in the
          code.
        </p>

        <h2>Positive-unlabeled learning</h2>
        <p>
          The founding problem: we have ~10² known deposits and ~10⁴–10⁶ cells,
          and the cells without a deposit are <i>unlabeled</i>, not negative —
          some of them host orebodies nobody has drilled. Treating them as
          negatives teaches the model that undiscovered deposits look like
          background.
        </p>

        <Paper
          title="A bagging SVM to learn from positive and unlabeled examples"
          authors="Mordelet & Vert (2014)"
          venue="Pattern Recognition Letters 37"
          href="https://doi.org/10.1016/j.patrec.2013.06.010"
        >
          <b>Applied in</b> <code>mpm/pu.py (PUBagger)</code>. Each of 25
          ensemble members trains on all positives plus a fresh random draw of
          unlabeled cells cast as temporary negatives. A hidden positive gets
          contradicted by the bags that did not draw it instead of being
          burned in by all of them, and the spread across bags falls out as
          the uncertainty layer on the map.
        </Paper>

        <Paper
          title="Learning classifiers from only positive and unlabeled data"
          authors="Elkan & Noto (2008)"
          venue="KDD 2008"
          href="https://doi.org/10.1145/1401890.1401920"
        >
          <b>Applied in</b> <code>mpm/pu.py (c_hat)</code>. Their constant
          <code> c = P(labeled | y = 1)</code> is estimated on positives each
          bag holds out — estimating it on training positives returns a
          meaningless ~1.0. Their theory assumes labels are missing at random
          (SCAR); exploration data violates that by construction, since
          discovery follows outcrop and road access. So <code>c_hat</code> is
          reported as a diagnostic, never a calibration guarantee, and the
          violation itself is measured separately by the bias audit.
        </Paper>

        <h2>Spatial cross-validation</h2>
        <p>
          Geoscience grids are smooth, so a random train/test split puts
          near-copies of every test cell into training and the model memorises
          location. The dashboard&apos;s &quot;validation honesty&quot; panel
          exists to price that in.
        </p>

        <Paper
          title="Cross-validation strategies for data with temporal, spatial, hierarchical, or phylogenetic structure"
          authors="Roberts et al. (2017)"
          venue="Ecography 40"
          href="https://doi.org/10.1111/ecog.02881"
        >
          <b>Applied in</b> <code>mpm/splits.py</code>. The case for blocking:
          hold out contiguous spatial blocks, not random cells. Blocks here are
          H3 parents at resolution 3 (~12,400 km²), clustered into regions and
          packed into folds by a longest-processing-time greedy that balances
          area and positive count simultaneously.
        </Paper>

        <Paper
          title="blockCV: an R package for generating spatially or environmentally separated folds"
          authors="Valavi et al. (2019)"
          venue="Methods in Ecology and Evolution 10"
          href="https://doi.org/10.1111/2041-210X.13107"
        >
          <b>Applied in</b> <code>mpm/splits.py (buffered_train_mask)</code>.
          The buffering discipline: beyond holding out blocks, training cells
          within 50–75 km of any held-out cell are dropped, killing the halo of
          spatially autocorrelated training data just across the fold boundary.
          Done with an exact haversine BallTree rather than a block-quantised
          approximation.
        </Paper>

        <h2>Evaluation that answers the operational question</h2>

        <Paper
          title="Validation of spatial prediction models for landslide hazard mapping"
          authors="Chung & Fabbri (2003)"
          venue="Natural Hazards 30 — the canonical prediction-rate curve reference"
          href="https://scholar.google.com/scholar?q=%22Validation+of+spatial+prediction+models+for+landslide+hazard+mapping%22"
        >
          <b>Applied in</b> <code>mpm/evaluate.py (capture_curve)</code>. The
          capture (prediction-rate) curve answers what a drilling budget
          actually asks: <i>if I can work the top X% of this land package,
          what share of the known endowment do I capture?</i> H3 cells are
          near-equal-area, so &quot;top 5% of cells&quot; is &quot;top 5% of
          area&quot; for free. ROC-AUC is reported only for comparability with
          the literature — at a 1-in-124 base rate it is dominated by how well
          the model orders barren ground against itself.
        </Paper>

        <h2>The benchmark this imitates</h2>

        <Paper
          title="Data-driven prospectivity modelling of sediment-hosted Zn–Pb mineral systems and their critical raw materials"
          authors="Lawley et al. (2022)"
          venue="Ore Geology Reviews 141:104635"
          href="https://doi.org/10.1016/j.oregeorev.2021.104635"
        >
          <b>Applied everywhere.</b> The template for the whole cube design:
          H3-indexed covariates joined from national geophysics, geology and
          geochemistry, with deposit labels from public occurrence databases.
          Their released datacube (NRCan Open File 8836,{' '}
          <a href="https://doi.org/10.4095/329203" target="_blank" rel="noreferrer">
            doi:10.4095/329203
          </a>) is directly loadable via <code>scripts/prepare_cube.py</code>.
          Their honest numbers — PR-AUC in the few-percent range — are the
          calibration for what &quot;good&quot; looks like here; published
          models reporting 0.99 AUC are usually reporting leakage.
        </Paper>

        <Paper
          title="GFM4MPM: Towards Geospatial Foundation Models for Mineral Prospectivity Mapping"
          authors="Mohammadi et al. (2024)"
          venue="arXiv:2406.12756"
          href="https://arxiv.org/abs/2406.12756"
        >
          <b>Applied in</b> <code>mpm/pu.py (informed negatives)</code>. Their
          likely-negative undersampling: a first-pass model scores the
          unlabeled pool, and pseudo-negatives are then drawn preferentially
          from cells that pass judged <i>unlike</i> the positives — the
          tabular analogue of their masked-autoencoder scheme. Also the
          modern reference point for PU + spatial CV + effort separation used
          together.
        </Paper>

        <h2>Data sources</h2>
        <table>
          <thead>
            <tr><th>Dataset</th><th>Role here</th><th>Link</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>USGS Mineral Resources Data System (MRDS)</td>
              <td>Deposit labels; exploration-effort proxies</td>
              <td><a href="https://mrdata.usgs.gov/mrds/" target="_blank" rel="noreferrer">mrdata.usgs.gov/mrds</a></td>
            </tr>
            <tr>
              <td>Merged aeromagnetic grid of the conterminous US (Bankey et al., 2002)</td>
              <td>Covariates: anomaly + roughness; defines the cube&apos;s domain</td>
              <td><a href="https://mrdata.usgs.gov/magnetic/" target="_blank" rel="noreferrer">mrdata.usgs.gov/magnetic</a></td>
            </tr>
            <tr>
              <td>US gravity anomaly grids (Phillips et al., 1993)</td>
              <td>Covariates: isostatic residual + Bouguer</td>
              <td><a href="https://mrdata.usgs.gov/gravity/" target="_blank" rel="noreferrer">mrdata.usgs.gov/gravity</a></td>
            </tr>
            <tr>
              <td>USGS National Geochemical Database, stream sediments</td>
              <td>Covariates: 8-element chemistry; sampling-density effort proxy</td>
              <td><a href="https://mrdata.usgs.gov/ngdb/sediment/" target="_blank" rel="noreferrer">mrdata.usgs.gov/ngdb/sediment</a></td>
            </tr>
          </tbody>
        </table>
        <p className="form-note">
          All public domain. Exact URLs, checksums and fetch timestamps for any
          given run are recorded in that run&apos;s provenance (visible on the
          map page).
        </p>
      </div>
    </div>
  )
}
