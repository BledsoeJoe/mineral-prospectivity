import React from 'react'

function Tile({ label, value, detail }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {detail && <div className="detail">{detail}</div>}
    </div>
  )
}

const fmt = (v, d = 3) => (Number.isFinite(v) ? v.toFixed(d) : '—')
const pct = (v) => (Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : '—')

export default function KpiRow({ report }) {
  const pooled = report.geology?.pooled ?? {}
  const spread = report.geology?.summary ?? {}
  const sd = (k) => (spread[k] ? ` ± ${fmt(spread[k][1], 2)} across folds` : null)
  const baseRate = pooled.base_rate

  return (
    <div className="card">
      <h2>Geology-only model · spatial block CV</h2>
      <div className="kpi-row">
        <Tile
          label="Capture @ 5% area"
          value={pct(pooled['capture@5%'])}
          detail={sd('capture@5%')}
        />
        <Tile
          label="Capture @ 10% area"
          value={pct(pooled['capture@10%'])}
          detail={sd('capture@10%')}
        />
        <Tile label="Lift @ 5%" value={`${fmt(pooled['lift@5%'], 1)}×`} detail="vs random ranking" />
        <Tile label="PR-AUC" value={fmt(pooled.pr_auc)} detail={`ROC-AUC ${fmt(pooled.roc_auc)}`} />
        <Tile
          label="Base rate"
          value={baseRate ? `1 in ${Math.round(1 / baseRate).toLocaleString()}` : '—'}
          detail="cells hosting a known deposit"
        />
        <Tile
          label="Cells scored"
          value={(pooled.cells_scored ?? 0).toLocaleString()}
          detail="each by a model that never saw it"
        />
      </div>
      <div className="note">
        Pooled out-of-fold metrics. Capture@5% answers the operational question:
        work the top 5% of ground, find this share of the known endowment.
      </div>
    </div>
  )
}
