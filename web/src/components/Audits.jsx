import React from 'react'

const pct = (v) => (Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : '\u2013')

export function OptimismCard({ optimism }) {
  return (
    <div className="card">
      <h2>Validation honesty</h2>
      <table className="audit">
        <tbody>
          <tr>
            <td>capture@5%, spatial block CV</td>
            <td>{pct(optimism['spatial_cv_capture@5%'])}</td>
          </tr>
          <tr>
            <td>capture@5%, random k-fold</td>
            <td>{pct(optimism['random_cv_capture@5%'])}</td>
          </tr>
          <tr>
            <td>optimism manufactured by the leak</td>
            <td>{pct(optimism.capture_inflation)}</td>
          </tr>
        </tbody>
      </table>
      <div className="note">
        Random k-fold lets the model memorize location: near-duplicates of every
        test cell sit in training. The spatial number is the one a drilling
        budget should believe.
      </div>
    </div>
  )
}

export function BiasCard({ audit }) {
  const share = audit.effort_importance_share ?? 0
  return (
    <div className="card">
      <h2>Exploration-bias audit</h2>
      <table className="audit">
        <tbody>
          <tr>
            <td>geology only, capture@5%</td>
            <td>{pct(audit['geology_only_capture@5%'])}</td>
          </tr>
          <tr>
            <td>with effort proxies</td>
            <td>{pct(audit['with_effort_capture@5%'])}</td>
          </tr>
          <tr>
            <td>gain from knowing where people looked</td>
            <td>{pct(audit.effort_gain)}</td>
          </tr>
        </tbody>
      </table>
      <div style={{ marginTop: 10 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
          <span style={{ color: 'var(--text-secondary)' }}>
            effort share of combined-model importance
          </span>
          <span style={{ fontWeight: 600 }}>{pct(share)}</span>
        </div>
        <div
          style={{
            marginTop: 4, height: 8, borderRadius: 4,
            background: 'color-mix(in srgb, var(--series-2) 18%, var(--surface-1))',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${Math.min(100, share * 100)}%`, height: '100%',
              background: 'var(--series-2)', borderRadius: 4,
            }}
          />
        </div>
      </div>
      <div className="note">
        The combined model scores better by rediscovering exploration history.
        Its extra skill goes to ground that has already been drilled; the
        headline model is quarantined from every orange covariate.
      </div>
    </div>
  )
}

export function DiscoveryCard({ audit }) {
  return (
    <div className="card">
      <h2>Discovery audit · hidden truth</h2>
      <div className="kpi-row">
        <div className="tile">
          <div className="label">Known captured @ 5%</div>
          <div className="value">{pct(audit['known_capture@5%'])}</div>
          <div className="detail">{audit.n_discovered?.toFixed(0)} discovered deposits</div>
        </div>
        <div className="tile">
          <div className="label">Undiscovered @ 5%</div>
          <div className="value">{pct(audit['undiscovered_capture@5%'])}</div>
          <div className="detail">
            {(audit.n_true_deposits - audit.n_discovered).toFixed(0)} hidden deposits
          </div>
        </div>
        <div className="tile">
          <div className="label">Discovery rate</div>
          <div className="value">{pct(audit.discovery_rate)}</div>
          <div className="detail">of all deposits that exist</div>
        </div>
      </div>
      <div className="note">
        Only measurable on the synthetic cube, where the generator knows every
        deposit that exists. A model that ranks deposits it was never shown as
        well as known ones is reading geology, not exploration history.
      </div>
    </div>
  )
}

export function ProvenanceCard({ report }) {
  const prov = report.provenance ?? []
  const cube = report.cube ?? {}
  return (
    <div className="card">
      <h2>Data provenance</h2>
      <table className="audit">
        <tbody>
          <tr>
            <td>cells</td>
            <td>{(cube.cells ?? 0).toLocaleString()}</td>
          </tr>
          <tr>
            <td>labeled deposit cells</td>
            <td>{(cube.positives ?? 0).toLocaleString()}</td>
          </tr>
          {prov.map((p) => (
            <tr key={p.source ?? 'synthetic'}>
              <td>
                {p.source}
                {p.url && (
                  <span style={{ color: 'var(--text-muted)' }}>
                    {' '}· {(p.bytes / 1e6).toFixed(0)} MB
                  </span>
                )}
              </td>
              <td style={{ fontFamily: 'monospace', fontSize: 11, fontWeight: 400 }}>
                {p.sha256 ? p.sha256.slice(0, 12) : `seed ${p.seed}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="note">
        Every artifact traces to the exact bytes it was built from; checksums
        come from the pipeline&apos;s stage manifests.
      </div>
    </div>
  )
}
