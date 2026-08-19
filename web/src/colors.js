// Color ramps from the design tokens in theme.css, in array form for deck.gl.
// Sequential = one hue, light -> dark. The score surface uses percentile
// binning: raw PU scores are log-skewed, and a linear ramp would paint the
// whole country in the two lightest steps.

export const BLUE_RAMP = [
  '#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5',
  '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b',
]

// Second sequential context: the categorical slot-2 hue as its own ramp.
export const ORANGE_RAMP = [
  '#fbe3d6', '#f8cdb5', '#f4b48f', '#f09a6a', '#eb8149', '#eb6834', '#d95926',
  '#c04a1c', '#a03d16', '#803011', '#66260d',
]

// Spatial CV folds are region identity; 5 large contiguous blocks. This is
// past the strict 3-series all-pairs cap, accepted deliberately: the blocks
// are huge and labeled in the tooltip, so precise hue decoding is never
// required. Slots 1, 2, 7, 4, 5 -- skips the aqua/green pair, which is the
// one confusable neighborhood on a map where any two folds can touch.
export const FOLD_COLORS = ['#2a78d6', '#eb6834', '#4a3aa7', '#eda100', '#e87ba4']

export const INK = [11, 11, 11]
export const MAGENTA = [232, 123, 164] // undiscovered truth (synth only)

export function hexToRgb(hex) {
  const v = parseInt(hex.slice(1), 16)
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255]
}

/** Map each value to its percentile rank in `values` (0..1). */
export function percentileScale(values) {
  const sorted = Array.from(values).filter(Number.isFinite).sort((a, b) => a - b)
  const n = sorted.length
  return (v) => {
    if (!Number.isFinite(v) || n === 0) return 0
    // binary search: count of values <= v
    let lo = 0
    let hi = n
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (sorted[mid] <= v) lo = mid + 1
      else hi = mid
    }
    return lo / n
  }
}

export function rampColor(ramp, t) {
  const rgb = ramp.map(hexToRgb)
  const i = Math.min(rgb.length - 1, Math.max(0, Math.floor(t * rgb.length)))
  return rgb[i]
}
