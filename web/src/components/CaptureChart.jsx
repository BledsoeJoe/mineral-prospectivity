import React, { useMemo, useRef, useState } from 'react'

// Capture (prediction-rate) curves: share of known deposits found as a
// function of ranked area accepted. All curves converge at (1,1), so identity
// is carried by the legend + hover tooltip rather than end labels.

const SERIES = [
  { key: 'geology', label: 'Geology only (spatial CV)', color: 'var(--series-1)' },
  { key: 'combined', label: 'With effort proxies', color: 'var(--series-2)' },
  { key: 'naive_cv', label: 'Geology (random k-fold)', color: 'var(--series-3)' },
]

const W = 392
const H = 250
const M = { l: 42, r: 14, t: 12, b: 34 }
const PW = W - M.l - M.r
const PH = H - M.t - M.b

const sx = (a) => M.l + a * PW
const sy = (c) => M.t + (1 - c) * PH

function interp(curve, a) {
  const { area, captured } = curve
  let lo = 0
  let hi = area.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (area[mid] < a) lo = mid + 1
    else hi = mid
  }
  if (lo === 0) return captured[0]
  const a0 = area[lo - 1]; const a1 = area[lo]
  const t = a1 === a0 ? 0 : (a - a0) / (a1 - a0)
  return captured[lo - 1] + t * (captured[lo] - captured[lo - 1])
}

export default function CaptureChart({ curves }) {
  const wrapRef = useRef(null)
  const [hover, setHover] = useState(null) // area fraction under cursor

  const present = SERIES.filter((s) => curves[s.key])

  const paths = useMemo(
    () => present.map((s) => {
      const { area, captured } = curves[s.key]
      const d = area
        .map((a, i) => `${i ? 'L' : 'M'}${sx(a).toFixed(1)},${sy(captured[i]).toFixed(1)}`)
        .join('')
      return { ...s, d }
    }),
    [curves],
  )

  const onMove = (e) => {
    const rect = wrapRef.current.querySelector('svg').getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * W
    const a = Math.min(1, Math.max(0, (px - M.l) / PW))
    setHover(a)
  }

  const yTicks = [0, 0.25, 0.5, 0.75, 1]
  const xTicks = [0, 0.25, 0.5, 0.75, 1]

  return (
    <div className="card" style={{ position: 'relative' }} ref={wrapRef}>
      <h2>Capture curves</h2>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 8 }}>
        {present.map((s) => (
          <div className="legend-row" key={s.key}>
            <span className="dot" style={{ background: s.color }} />
            {s.label}
          </div>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', display: 'block' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} stroke="var(--grid)" strokeWidth="1" />
            <text
              x={M.l - 6}
              y={sy(t) + 3.5}
              textAnchor="end"
              fontSize="10"
              fill="var(--text-muted)"
            >
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text
            key={t}
            x={sx(t)}
            y={H - M.b + 14}
            textAnchor="middle"
            fontSize="10"
            fill="var(--text-muted)"
          >
            {Math.round(t * 100)}%
          </text>
        ))}
        <text
          x={M.l + PW / 2}
          y={H - 4}
          textAnchor="middle"
          fontSize="10"
          fill="var(--text-secondary)"
        >
          share of area accepted (ranked by score)
        </text>

        {/* random-ranking reference: capture == area */}
        <line
          x1={sx(0)} y1={sy(0)} x2={sx(1)} y2={sy(1)}
          stroke="var(--baseline)" strokeWidth="1"
        />

        {/* the 5% operating point the KPIs quote */}
        <line
          x1={sx(0.05)} x2={sx(0.05)} y1={M.t} y2={M.t + PH}
          stroke="var(--baseline)" strokeWidth="1"
        />
        <text x={sx(0.05) + 3} y={M.t + 9} fontSize="9" fill="var(--text-muted)">5%</text>

        {paths.map((p) => (
          <path
            key={p.key}
            d={p.d}
            fill="none"
            stroke={p.color}
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}

        {hover !== null && (
          <g>
            <line
              x1={sx(hover)} x2={sx(hover)} y1={M.t} y2={M.t + PH}
              stroke="var(--text-muted)" strokeWidth="1"
            />
            {present.map((s) => (
              <circle
                key={s.key}
                cx={sx(hover)}
                cy={sy(interp(curves[s.key], hover))}
                r="4"
                fill={s.color}
                stroke="var(--surface-1)"
                strokeWidth="2"
              />
            ))}
          </g>
        )}
      </svg>
      {hover !== null && (
        <div
          className="chart-tooltip"
          style={{
            left: `${Math.min(78, (sx(hover) / W) * 100)}%`,
            top: 90,
          }}
        >
          <div className="t-title">top {(hover * 100).toFixed(1)}% of area</div>
          {present.map((s) => (
            <div className="t-row" key={s.key}>
              <span className="dot" style={{ background: s.color }} />
              <span>{s.label.split(' (')[0]}</span>
              <span className="v">{(interp(curves[s.key], hover) * 100).toFixed(1)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
