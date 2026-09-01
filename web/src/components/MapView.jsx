import React, { useEffect, useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { WebMercatorViewport } from '@deck.gl/core'
import { TileLayer, H3HexagonLayer } from '@deck.gl/geo-layers'
import { BitmapLayer, ScatterplotLayer } from '@deck.gl/layers'
import { cellToLatLng } from 'h3-js'
import {
  BLUE_RAMP, ORANGE_RAMP, FOLD_COLORS, INK, MAGENTA,
  hexToRgb, percentileScale, rampColor,
} from '../colors.js'

const CARTO_KEY = import.meta.env.VITE_CARTO_KEY;
const LAYER_MODES = [
  { id: 'score', label: 'Prospectivity' },
  { id: 'uncertainty', label: 'Uncertainty' },
  { id: 'fold', label: 'CV folds' },
]

function useDarkMode() {
  const [dark, setDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  )
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e) => setDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return dark
}

function initialViewState(cells) {
  // Sample centroids for bounds; 500 points describe the extent fine.
  const step = Math.max(1, Math.floor(cells.length / 500))
  let minLat = 90; let maxLat = -90; let minLon = 180; let maxLon = -180
  for (let i = 0; i < cells.length; i += step) {
    const [lat, lon] = cellToLatLng(cells[i].h)
    if (lat < minLat) minLat = lat
    if (lat > maxLat) maxLat = lat
    if (lon < minLon) minLon = lon
    if (lon > maxLon) maxLon = lon
  }
  const vp = new WebMercatorViewport({ width: 900, height: 620 })
  const { longitude, latitude, zoom } = vp.fitBounds(
    [[minLon, minLat], [maxLon, maxLat]], { padding: 40 },
  )
  return { longitude, latitude, zoom: Math.min(zoom, 8) }
}

export default function MapView({ cells, hasTruth }) {
  const dark = useDarkMode()
  const [mode, setMode] = useState('score')
  const [topFrac, setTopFrac] = useState(0.05)
  const [showTop, setShowTop] = useState(true)
  const [showDeposits, setShowDeposits] = useState(true)
  const [showTruth, setShowTruth] = useState(false)

  const viewState = useMemo(() => initialViewState(cells), [cells])

  const scorePct = useMemo(() => percentileScale(cells.map((c) => c.s)), [cells])
  const stdPct = useMemo(() => percentileScale(cells.map((c) => c.u)), [cells])

  const topThreshold = useMemo(() => {
    const scores = cells.map((c) => c.s).filter(Number.isFinite).sort((a, b) => b - a)
    const k = Math.max(0, Math.min(scores.length - 1, Math.round(topFrac * scores.length) - 1))
    return scores.length ? scores[k] : Infinity
  }, [cells, topFrac])

  const deposits = useMemo(
    () => cells.filter((c) => c.y === 1).map((c) => {
      const [lat, lon] = cellToLatLng(c.h)
      return { position: [lon, lat], h: c.h }
    }),
    [cells],
  )
  const undiscovered = useMemo(
    () => (hasTruth
      ? cells.filter((c) => c.t === 1 && c.y === 0).map((c) => {
        const [lat, lon] = cellToLatLng(c.h)
        return { position: [lon, lat], h: c.h }
      })
      : []),
    [cells, hasTruth],
  )

  // On a dark surface the light end of a sequential ramp glows, so "near
  // zero" must sit at the dark end instead: same hue, direction flipped.
  const activeRamp = useMemo(() => {
    const ramp = mode === 'uncertainty' ? ORANGE_RAMP : BLUE_RAMP
    return dark ? [...ramp].reverse() : ramp
  }, [mode, dark])

  // "Top N%" is an emphasis treatment, not an outline: at country zoom a
  // stroke per tiny hex smears into solid blobs. Instead the tail fades to a
  // neutral wash and the shortlist keeps its color.
  const deemph = dark ? [58, 58, 55, 120] : [225, 224, 217, 150]

  const getFillColor = useMemo(() => {
    if (mode === 'fold') return (d) => hexToRgb(FOLD_COLORS[((d.f % 5) + 5) % 5])
    if (mode === 'uncertainty') return (d) => rampColor(activeRamp, stdPct(d.u))
    return (d) => {
      if (showTop && d.s < topThreshold) return deemph
      return rampColor(activeRamp, scorePct(d.s))
    }
  }, [mode, scorePct, stdPct, activeRamp, showTop, topThreshold, dark])

  const layers = [
    new TileLayer({
      id: 'basemap',
      data: dark
        ? `https://basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png?key=${CARTO_KEY}`
        : `https://basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png?key=${CARTO_KEY}`,
      minZoom: 0,
      maxZoom: 19,
      tileSize: 256,
      renderSubLayers: (props) => {
        const [[west, south], [east, north]] = props.tile.boundingBox
        return new BitmapLayer(props, {
          data: null,
          image: props.data,
          bounds: [west, south, east, north],
        })
      },
    }),
    new H3HexagonLayer({
      id: 'cells',
      data: cells,
      getHexagon: (d) => d.h,
      filled: true,
      stroked: false,
      extruded: false,
      getFillColor,
      opacity: 0.55,
      pickable: true,
      updateTriggers: { getFillColor: [mode, dark, showTop, topThreshold] },
    }),
    showDeposits && new ScatterplotLayer({
      id: 'deposits',
      data: deposits,
      getPosition: (d) => d.position,
      getFillColor: dark ? [255, 255, 255] : INK,
      getLineColor: dark ? [26, 26, 25] : [252, 252, 251],
      stroked: true,
      lineWidthMinPixels: 2,
      radiusMinPixels: 3,
      radiusMaxPixels: 6,
    }),
    showTruth && hasTruth && new ScatterplotLayer({
      id: 'undiscovered',
      data: undiscovered,
      getPosition: (d) => d.position,
      getFillColor: MAGENTA,
      getLineColor: dark ? [26, 26, 25] : [252, 252, 251],
      stroked: true,
      lineWidthMinPixels: 2,
      radiusMinPixels: 3,
      radiusMaxPixels: 6,
    }),
  ].filter(Boolean)

  const getTooltip = ({ object, layer }) => {
    if (!object) return null
    if (layer.id === 'deposits') return { html: '<b>Known deposit</b>', className: 'deck-tooltip' }
    if (layer.id === 'undiscovered') {
      return { html: '<b>Undiscovered deposit</b> (hidden truth)', className: 'deck-tooltip' }
    }
    if (layer.id !== 'cells') return null
    const pct = Math.round(scorePct(object.s) * 100)
    return {
      className: 'deck-tooltip',
      html: `
        <div style="font-weight:600">${object.h}</div>
        <div>score ${object.s?.toFixed(4) ?? '\u2013'} · P${pct}</div>
        <div>ensemble spread ${object.u?.toFixed(4) ?? '\u2013'}</div>
        <div>fold ${object.f}${object.y ? ' · <b>known deposit</b>' : ''}</div>`,
    }
  }

  return (
    <div className="map-pane">
      <div className="map-controls">
        <div className="card">
          <div className="seg" role="tablist">
            {LAYER_MODES.map((m) => (
              <button
                key={m.id}
                className={mode === m.id ? 'on' : ''}
                onClick={() => setMode(m.id)}
              >
                {m.label}
              </button>
            ))}
          </div>
          {mode !== 'fold' && (
            <div className="ramp-legend" style={{ marginTop: 8 }}>
              <div className="swatches">
                {activeRamp.map((c) => <div key={c} style={{ background: c }} />)}
              </div>
              <div className="ends">
                <span>P0</span>
                <span>{mode === 'uncertainty' ? 'ensemble spread percentile' : 'score percentile'}</span>
                <span>P100</span>
              </div>
            </div>
          )}
          {mode === 'fold' && (
            <div className="note" style={{ marginTop: 6 }}>
              Held-out spatial blocks; each cell is scored by a model that never
              saw its fold (hover a cell for its fold id).
            </div>
          )}
        </div>
        <div className="card">
          {mode === 'score' && (
            <div className="slider-row">
              <input
                type="checkbox"
                checked={showTop}
                onChange={(e) => setShowTop(e.target.checked)}
              />
              <span style={{ whiteSpace: 'nowrap' }}>top {(topFrac * 100).toFixed(0)}%</span>
              <input
                type="range"
                min="1"
                max="20"
                value={Math.round(topFrac * 100)}
                onChange={(e) => setTopFrac(Number(e.target.value) / 100)}
              />
            </div>
          )}
          <label className="check-row">
            <input
              type="checkbox"
              checked={showDeposits}
              onChange={(e) => setShowDeposits(e.target.checked)}
            />
            <span className="dot" style={{ background: 'var(--text-primary)' }} />
            known deposits
          </label>
          {hasTruth && (
            <label className="check-row">
              <input
                type="checkbox"
                checked={showTruth}
                onChange={(e) => setShowTruth(e.target.checked)}
              />
              <span className="dot" style={{ background: 'var(--series-5)' }} />
              undiscovered (hidden truth)
            </label>
          )}
        </div>
      </div>
      <DeckGL
        initialViewState={viewState}
        controller={{ dragRotate: false }}
        layers={layers}
        getTooltip={getTooltip}
      />
    </div>
  )
}
