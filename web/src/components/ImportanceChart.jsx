import React, { useState } from 'react'

// Covariate importance, colored by group: the point of this chart is not the
// ranking, it is *which group* the model leans on. An honest geology model
// leans blue; a combined model leaning orange is reading exploration history.

const GROUP_COLOR = { geology: 'var(--series-1)', effort: 'var(--series-2)' }

const ROW_H = 22
const BAR_H = 14
const W = 392
const LABEL_W = 168
const VALUE_W = 40

// 4px rounded data-end, square at the baseline (left).
function barPath(x, y, w, h) {
  const r = Math.min(4, w)
  return `M${x},${y} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${h - 2 * r} a${r},${r} 0 0 1 -${r},${r} h${-(w - r)} z`
}

export default function ImportanceChart({ importance }) {
  const models = Object.keys(importance)
  const [model, setModel] = useState(models.includes('combined') ? 'combined' : models[0])
  const rows = (importance[model] ?? []).slice(0, 12)
  const max = Math.max(...rows.map((r) => r.importance), 1e-9)
  const height = rows.length * ROW_H + 6

  return (
    <div className="card">
      <h2>Covariate importance</h2>
      <div className="seg" style={{ marginBottom: 8 }}>
        {models.map((m) => (
          <button key={m} className={model === m ? 'on' : ''} onClick={() => setModel(m)}>
            {m === 'naive_cv' ? 'random k-fold' : m}
          </button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 14, marginBottom: 6 }}>
        <div className="legend-row">
          <span className="dot" style={{ background: GROUP_COLOR.geology }} />geology
        </div>
        <div className="legend-row">
          <span className="dot" style={{ background: GROUP_COLOR.effort }} />effort proxy
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${height}`} style={{ width: '100%', display: 'block' }}>
        {rows.map((r, i) => {
          const y = i * ROW_H + (ROW_H - BAR_H) / 2
          const w = Math.max(2, (r.importance / max) * (W - LABEL_W - VALUE_W))
          return (
            <g key={r.feature}>
              <text
                x={LABEL_W - 6}
                y={y + BAR_H - 3.5}
                textAnchor="end"
                fontSize="10"
                fill="var(--text-secondary)"
              >
                {r.feature.length > 26 ? `${r.feature.slice(0, 25)}…` : r.feature}
                <title>{r.feature}</title>
              </text>
              <path d={barPath(LABEL_W, y, w, BAR_H)} fill={GROUP_COLOR[r.group]}>
                <title>{`${r.feature}: ${(r.importance * 100).toFixed(1)}% (${r.group})`}</title>
              </path>
              <text
                x={LABEL_W + w + 5}
                y={y + BAR_H - 3.5}
                fontSize="10"
                fill="var(--text-muted)"
              >
                {(r.importance * 100).toFixed(1)}%
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
