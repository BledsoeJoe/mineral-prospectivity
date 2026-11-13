import React, { useEffect, useState } from 'react'
import { fetchProfiles, fetchReport } from '../api.js'

const pct = (v, d = 1) => (Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : '–')
const mb = (b) => (Number.isFinite(b) ? `${(b / 2 ** 20).toFixed(1)} MB` : '–')

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

  const cube = report?.cube ?? {}
  const opt = report?.optimism ?? {}
  const bias = report?.bias_audit ?? {}
  const provenance = report?.provenance ?? []

  return (
    <div className="prose-page">
      <div className="prose">
        <h1>From download to hexagon</h1>
        <p className="lede">
          The path public exploration data takes through this system: what
          arrives, what is wrong with it, how it becomes one consistent grid,
          and how each run is published for people and programs to use.
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

        <h2>What arrives</h2>
        <p>
          Every run records exactly which files it was built from. Downloads
          stream into a local cache under a temporary name and are renamed only
          when complete, so a killed transfer can never pass for a finished
          file, and each file&apos;s sha256 is computed as it streams. The
          checksums are recorded rather than pinned: the USGS revises these
          compilations in place, so a changed checksum should rebuild what
          depends on that file, not fail the run.
        </p>
        {provenance.length > 0 && (
          <table>
            <thead><tr><th>Source</th><th>Size</th><th>sha256</th><th>This run</th></tr></thead>
            <tbody>
              {provenance.map((p) => (
                <tr key={p.source}>
                  <td><a href={p.url} target="_blank" rel="noreferrer">{p.source}</a></td>
                  <td>{mb(p.bytes)}</td>
                  <td><code>{p.sha256?.slice(0, 12)}…</code></td>
                  <td>{p.from_cache ? 'cached' : 'downloaded'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h2>What is wrong with it</h2>
        <p>
          Public geoscience data is accurate and messy. None of the problems
          below raise an error if ignored; each one just produces a map that is
          quietly wrong.
        </p>
        <table>
          <thead><tr><th>Problem</th><th>What it looks like</th><th>What the pipeline does</th></tr></thead>
          <tbody>
            <tr>
              <td><b>Censored chemistry</b></td>
              <td>
                Results below a lab&apos;s detection limit are stored as the
                negative of that limit. About 41% of stream-sediment
                measurements are censored.
              </td>
              <td>Substitutes half the detection limit, a standard convention, instead of keeping negative concentrations.</td>
            </tr>
            <tr>
              <td><b>Mixed units</b></td>
              <td>ppm and percent in the same column.</td>
              <td>Converts percent to ppm, then takes log10, since concentrations span four orders of magnitude.</td>
            </tr>
            <tr>
              <td><b>Invisible header bytes</b></td>
              <td>
                Some NGDB files begin with a UTF-8 byte-order mark and others
                do not, which glues stray characters onto the first column name.
              </td>
              <td>Strips them from headers, so the join key is never silently missing.</td>
            </tr>
            <tr>
              <td><b>Null Island</b></td>
              <td>Legacy MRDS records located at latitude 0, longitude 0.</td>
              <td>Dropped.</td>
            </tr>
            <tr>
              <td><b>Free-text commodities</b></td>
              <td>&quot;Copper, Gold, Silver&quot; spread across three text fields.</td>
              <td>Matches full commodity names across all three.</td>
            </tr>
            <tr>
              <td><b>One deposit, many records</b></td>
              <td>
                Occurrence databases are compilations of compilations; the same
                orebody can appear under several names a kilometer apart.
              </td>
              <td>Deduplicates by normalized name within a cell, then by cell, so well-explored districts are not counted twice.</td>
            </tr>
          </tbody>
        </table>

        <h2>Gates between stages</h2>
        <p>
          A pipeline that only fails on exceptions fails too late: pandas will
          happily build a grid from a half-parsed file. Each stage checks its
          output before the next one reads it: required columns, a minimum row
          count, a row ceiling (which catches format drift like a delimiter
          change that splits rows apart), coordinate bounds, uniqueness, and
          null fractions. Problems that would make the output wrong stop the
          run with a message naming the file and the number; softer ones are
          logged as warnings. The label stage refuses to continue with fewer
          than 30 deposit cells, because spatial validation on fewer is noise.
          Every result is saved next to the data it describes.
        </p>

        <h2>One grid</h2>
        <p>
          Everything is joined onto <a href="https://h3geo.org" target="_blank" rel="noreferrer">H3</a>{' '}
          hexagons of about 250 km² each. For the US copper profile, about 2.5
          million aeromagnetic grid points and 700 thousand gravity points
          reduce to a mean and a roughness per cell, and 213 thousand
          stream-sediment samples reduce to a median per element per cell. The
          magnetic grid defines the domain: <Live>{(cube.cells ?? 0).toLocaleString()}</Live>{' '}
          cells in this run.
        </p>
        <p>
          Joining surfaces a question every column has to answer: what does an
          empty value mean? Counts become zero, because no record in a cell is
          itself information. Measurements stay missing and are filled in
          later, because no sample is not the same as zero copper. Geochemistry
          covers fewer than half the cells, so this distinction touches most of
          the grid.
        </p>

        <h2>Labels are a data decision</h2>
        <p>
          A cell is labeled a deposit if it holds an MRDS record that is a
          producer or past producer of the target commodity with a recorded
          production size of large or medium. For the US copper profile:
        </p>
        <table>
          <thead><tr><th>Filter</th><th>Records left</th></tr></thead>
          <tbody>
            <tr><td>MRDS records in the conterminous US</td><td>257,783</td></tr>
            <tr><td>mention copper</td><td>32,172</td></tr>
            <tr><td>producer or past producer</td><td>16,542</td></tr>
            <tr><td>large or medium production</td><td>759 sites, in 358 cells</td></tr>
          </tbody>
        </table>
        <p>
          Labeling every copper record instead would mark about 4,350 cells,
          twelve times as many, mostly prospects and roadside showings; the
          label would stop meaning <i>orebody</i>. That choice moves results
          more than any modeling choice, and it is the kind that should be
          made with geologists, not by a software engineer alone.
        </p>

        <h2>Names are a contract</h2>
        <p>
          Some columns describe the ground; others describe where people have
          looked: sampling density, distance to known deposits, whether a cell
          was surveyed at all. A model given the second kind learns exploration
          history and validates brilliantly against it. So columns are routed
          by name: anything matching patterns like <code>sample_density</code>,{' '}
          <code>dist_to_deposit</code>, <code>effort</code> or{' '}
          <code>_isna</code> is kept out of the headline model and only used
          to measure that bias. Renaming a column can move it across that
          line, so names here are reviewed like an interface.
        </p>
        <p>
          The same discipline applies to how a column is computed. An early
          version of the distance-to-nearest-deposit feature measured each
          deposit&apos;s distance to itself, which is zero, making the column a
          copy of the label; the model that saw it scored a perfect PR-AUC of
          1.000. It now excludes each cell&apos;s own deposit, and a test pins
          that. Lineage bugs tend to look like great results.
        </p>

        <h2>The published run</h2>
        <p>
          Each run is written once to its own directory, named by timestamp and
          a hash of its contents, and never modified. A small{' '}
          <code>latest.json</code> pointer is updated only after everything else
          is written, so readers never see a partial run.
        </p>
        <table>
          <thead><tr><th>File</th><th>For</th></tr></thead>
          <tbody>
            <tr><td><code>report.json</code></td><td>Metrics, audits, configuration, and the provenance above</td></tr>
            <tr><td><code>cells.json.gz</code></td><td>The map payload, compact and pre-compressed so the API sends it as is</td></tr>
            <tr><td><code>curves.json</code>, <code>importance.json</code></td><td>The dashboard charts</td></tr>
            <tr><td><code>scores.parquet</code></td><td>Every cell&apos;s score from every model, for analysis outside the app</td></tr>
          </tbody>
        </table>
        <p>
          The runs live in object storage, so the app is only one reader. A
          notebook or a query engine can read a run straight from the bucket,
          for example in DuckDB:
        </p>
        <pre>{`select h3, lat, lon, score, score_std
from 's3://<bucket>/<prefix>/runs/${profile ?? '<profile>'}/${report?.run_id ?? '<run_id>'}/scores.parquet'
where model = 'geology'
order by score desc
limit 100;`}</pre>

        <h2>How the output is checked</h2>
        <p>
          The headline number is how many known deposits land in the top 5% of
          ground the model ranks highest: <Live>{pct(bias['geology_only_capture@5%'])}</Live>{' '}
          in this run, from public geophysics and geochemistry alone. Two
          comparisons show what that number would look like if the pipeline
          were careless. A random train/test split, which leaks neighboring
          cells into training, would report <Live>{pct(opt['random_cv_capture@5%'])}</Live>{' '}
          instead of the spatially separated <Live>{pct(opt['spatial_cv_capture@5%'])}</Live>.
          Letting the effort columns in would report <Live>{pct(bias['with_effort_capture@5%'])}</Live>,
          by rediscovering where people have already looked. The{' '}
          <a href="/research">Research</a> page covers the methods behind
          these checks.
        </p>

        <div className="aside">
          If one sentence survives from this page: <b>most of what makes this
          map trustworthy happens before the model runs, in how the data is
          fetched, cleaned, gated, joined, labeled, and published.</b>
        </div>
      </div>
    </div>
  )
}
