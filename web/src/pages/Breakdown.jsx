import React, { useEffect, useState } from 'react'
import { fetchProfiles, fetchReport } from '../api.js'

const pct = (v, d = 1) => (Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : '\u2013')
const num = (v, d = 3) => (Number.isFinite(v) ? v.toFixed(d) : '\u2013')

function Live({ children }) {
  return <span className="metric-inline">{children}</span>
}

export default function Breakdown() {
  const [profiles, setProfiles] = useState([])
  const [profile, setProfile] = useState(null)
  const [report, setReport] = useState(null)

  useEffect(() => {
    fetchProfiles().then((ps) => {
      setProfiles(ps)
      if (ps.length) setProfile((ps.find((p) => p.profile !== 'synth') ?? ps[0]).profile)
    }).catch(() => {})
  }, [])
  useEffect(() => {
    if (profile) fetchReport(profile).then(setReport).catch(() => {})
  }, [profile])

  const g = report?.geology?.pooled ?? {}
  const opt = report?.optimism ?? {}
  const bias = report?.bias_audit ?? {}
  const cube = report?.cube ?? {}
  const disc = report?.discovery_audit

  return (
    <div className="prose-page">
      <div className="prose">
        <h1>What you are looking at</h1>
        <p className="lede">
          A plain-language guide to every number on the map page, and why the
          obvious ways of computing them give wrong answers.
        </p>

        {profiles.length > 1 && (
          <p className="form-note">
            Live numbers below are from the latest{' '}
            <select value={profile ?? ''} onChange={(e) => setProfile(e.target.value)}>
              {profiles.map((p) => (
                <option key={p.profile} value={p.profile}>{p.profile}</option>
              ))}
            </select>{' '}
            run.
          </p>
        )}

        <h2>The task</h2>
        <p>
          Every hexagon is an <a href="https://h3geo.org" target="_blank" rel="noreferrer">H3</a>{' '}
          cell of roughly equal area. The current cube has{' '}
          <Live>{(cube.cells ?? 0).toLocaleString()}</Live> cells, of which{' '}
          <Live>{(cube.positives ?? 0).toLocaleString()}</Live> host a known
          deposit, a base rate of about{' '}
          <Live>1 in {cube.base_rate ? Math.round(1 / cube.base_rate) : '\u2013'}</Live>.
          The model&apos;s job is to rank all of them so that the deposits pile
          up at the top of the ranking. Deposits are rare, spatially clustered,
          and, crucially, <i>known deposits are only the ones somebody
          already found</i>. Each of those three facts breaks a standard ML
          workflow in its own way; the three audits on the map page each
          correspond to one of them.
        </p>

        <h2>The score, and the uncertainty layer</h2>
        <p>
          The score is produced by <b>PU bagging</b>: 25 gradient-boosted-tree
          models, each trained on all known deposits plus a different random
          sample of unlabeled cells temporarily treated as barren. A
          cell&apos;s score is the ensemble&apos;s average vote that it looks
          deposit-like. The map colors by <b>percentile</b> of that score
          (raw values are tiny and log-skewed; coloring them linearly would
          paint the whole country in the two palest steps).
        </p>
        <p>
          The <b>uncertainty layer</b> is the disagreement between those 25
          models. It matters operationally: a high score every bag agrees on
          is a different proposition from a high score the ensemble is split
          over, and ranking by the mean alone throws that distinction away.
        </p>

        <h2>The metrics</h2>
        <table>
          <thead><tr><th>Metric</th><th>Current value</th><th>What it means</th></tr></thead>
          <tbody>
            <tr>
              <td><b>capture@5%</b></td>
              <td><Live>{pct(g['capture@5%'])}</Live></td>
              <td>
                Rank every cell, take the top 5% of area: what fraction of the
                known deposits sit inside it? This is the question a drilling
                budget asks, and the headline number of the project.
              </td>
            </tr>
            <tr>
              <td><b>lift@5%</b></td>
              <td><Live>{num(g['lift@5%'], 1)}×</Live></td>
              <td>
                capture@5% divided by 0.05: how much better than throwing
                darts. Random ranking scores 1×.
              </td>
            </tr>
            <tr>
              <td><b>PR-AUC</b></td>
              <td><Live>{num(g.pr_auc)}</Live></td>
              <td>
                Area under the precision-recall curve; the right summary
                statistic under extreme class imbalance. Published prospectivity
                studies with honest validation report values in the low
                percents, so small numbers here are normal, not broken.
              </td>
            </tr>
            <tr>
              <td><b>ROC-AUC</b></td>
              <td><Live>{num(g.roc_auc)}</Live></td>
              <td>
                Reported only because the literature does. At a 1-in-
                {cube.base_rate ? Math.round(1 / cube.base_rate) : '…'} base
                rate it is dominated by how well the model orders barren cells
                against other barren cells, and it barely moves when validation
                leaks, which is exactly why it flatters.
              </td>
            </tr>
            <tr>
              <td><b>± across folds</b></td>
              <td>\u2013</td>
              <td>
                Headline metrics are pooled over out-of-fold predictions (every
                cell scored by a model that never saw it). The per-fold spread
                is shown separately as a stability estimate; with only a few
                dozen positives per fold, per-fold means are mostly noise.
              </td>
            </tr>
            <tr>
              <td><b>c_hat</b></td>
              <td><Live>{num(report?.geology?.c_hat)}</Live></td>
              <td>
                Elkan &amp; Noto&apos;s P(labeled | deposit), estimated on
                held-out positives. A diagnostic of how recognizable the
                labeled deposits are to the model, <i>not</i> the true
                discovery rate, because deposit labeling is anything but
                random.
              </td>
            </tr>
          </tbody>
        </table>

        <h2>Failure mode #1: absence is not evidence of absence</h2>
        <p>
          Cells without a known deposit are unlabeled, not barren; some hide
          orebodies under cover. Train a standard classifier on them as
          negatives and it learns that undiscovered deposits look like
          background, which is precisely the class of cell exploration wants.
          The PU bagging scheme above is the mitigation: no single unlabeled
          cell is ever treated as definitively barren by the whole ensemble.
        </p>

        <h2>Failure mode #2: random cross-validation leaks</h2>
        <p>
          Geophysical surfaces are smooth and geology is contiguous, so a
          random train/test split puts near-duplicates of every test cell into
          training; the model memorizes location and the score inflates. The
          fix is <b>spatial block CV</b> (the &quot;CV folds&quot; layer on
          the map shows the actual held-out regions), plus a buffer that drops
          training cells within tens of km of any test cell.
        </p>
        <p>
          The dashboard runs the leaky protocol side-by-side to price it:
          spatial CV scores <Live>{pct(opt['spatial_cv_capture@5%'])}</Live>{' '}
          capture@5% and random k-fold says{' '}
          <Live>{pct(opt['random_cv_capture@5%'])}</Live>; the difference,{' '}
          <Live>{pct(opt.capture_inflation)}</Live>, is optimism manufactured
          purely by the split. Note how little ROC-AUC moves under the same
          leak ({num(opt.spatial_cv_roc_auc)} → {num(opt.random_cv_roc_auc)}):
          the standard metric is nearly blind to it.
        </p>

        <h2>Failure mode #3: deposits are recorded where people looked</h2>
        <p>
          Known deposits cluster near roads, outcrop and historic mining
          districts. Give a model covariates that encode <i>where people have
          looked</i> (occurrence density, distance to the nearest known
          deposit, geochemical sampling density) and it becomes a
          where-have-people-looked detector: it validates brilliantly against
          known deposits and sends every drill rig to ground the industry
          already knows.
        </p>
        <p>
          The control is a <b>quarantine</b>: covariates matching effort
          patterns are excluded from the headline model and given to a second,
          deliberately contaminated one. Currently the honest model captures{' '}
          <Live>{pct(bias['geology_only_capture@5%'])}</Live> at 5% area; the
          contaminated one captures <Live>{pct(bias['with_effort_capture@5%'])}</Live>,
          with <Live>{pct(bias.effort_importance_share)}</Live> of its
          importance sitting on effort covariates. That gap of{' '}
          <Live>{pct(bias.effort_gain)}</Live> is not extra skill. It is the
          model rediscovering exploration history.
        </p>
        {disc && (
          <>
            <h3>The discovery audit (synthetic profile only)</h3>
            <p>
              On real data you can only validate against <i>discovered</i>
              deposits, so an effort-reading model is unfalsifiable. The
              synthetic cube exists to close that loop: its generator knows
              every deposit, including the{' '}
              <Live>{(disc.n_true_deposits - disc.n_discovered).toFixed(0)}</Live>{' '}
              never &quot;found&quot; by its biased discovery process. The
              geology-only model captures{' '}
              <Live>{pct(disc['known_capture@5%'])}</Live> of known and{' '}
              <Live>{pct(disc['undiscovered_capture@5%'])}</Live> of
              undiscovered deposits in its top 5%. Ranking deposits it was
              never shown as well as ones it was is the whole point of the
              quarantine, and it is unmeasurable without a simulator.
            </p>
          </>
        )}

        <h2>The data, physically</h2>
        <table>
          <thead><tr><th>Covariate family</th><th>What it measures</th><th>Why it might find copper</th></tr></thead>
          <tbody>
            <tr>
              <td><code>mag_anomaly_nt</code> (mean, std)</td>
              <td>Aeromagnetic anomaly; within-cell roughness</td>
              <td>
                Magnetite-bearing intrusions and basement structure show
                through cover; texture flags structural complexity that
                localises mineralising fluids.
              </td>
            </tr>
            <tr>
              <td><code>grav_isostatic_mgal</code>, <code>grav_bouguer_mgal</code></td>
              <td>Gravity anomalies (upper-crust residual; total crustal)</td>
              <td>
                Dense intrusive complexes and uplifted basement read as highs;
                basins as lows. Porphyry systems ride on intrusions.
              </td>
            </tr>
            <tr>
              <td><code>geochem_*_log_ppm</code></td>
              <td>Stream-sediment chemistry (Cu, Zn, Pb, Ag, Mo, As, Fe, Mn), log-median per cell</td>
              <td>
                Erosion smears a deposit&apos;s footprint into drainage
                sediments; multi-element patterns are a classic direct
                detection signal.
              </td>
            </tr>
            <tr>
              <td><i>quarantined:</i> <code>effort_mrds_density</code>, <code>dist_to_deposit_km</code>, <code>sample_density_*</code>, <code>*_isna</code></td>
              <td>Exploration attention: record density, distance to known deposits, sampling coverage, survey gaps</td>
              <td>
                They &quot;find copper&quot; extremely well, by finding
                geologists. Which is why the headline model never sees them.
              </td>
            </tr>
          </tbody>
        </table>

        <h2>The labels, honestly</h2>
        <p>
          A positive cell contains at least one MRDS record that is a{' '}
          <b>Producer or Past Producer</b> of the target commodity with
          recorded production size <b>Large or Medium</b>. Loosen that to
          &quot;any copper occurrence&quot; and the label set balloons ~50×
          with roadside showings; the label stops meaning <i>orebody</i>.
          Two caveats stand: a cell is a cell (a world-class deposit and a
          modest one count the same), and byproduct producers (a lead mine
          with copper credits) count as copper. Both are label-definition
          decisions that move results more than any modeling choice, which is
          why they are documented rather than buried.
        </p>

        <div className="aside">
          If one sentence survives from this page: <b>the top-5% shortlist on
          the map captures {pct(bias['geology_only_capture@5%'])} of known
          large/medium copper producers using only public geophysics and
          geochemistry, and the audits around that number are what make it
          worth believing.</b>
        </div>
      </div>
    </div>
  )
}
