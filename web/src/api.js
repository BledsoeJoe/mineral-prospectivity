// Thin client for the read-only artifacts API. Everything is origin-relative:
// vite proxies /api in dev, nginx proxies it in the compose stack.

async function getJson(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`)
  return res.json()
}

export const fetchProfiles = () => getJson('/api/profiles')
export const fetchReport = (profile) => getJson(`/api/profiles/${profile}/runs/latest/report`)
export const fetchSources = () => getJson('/api/sources')

export async function postSource(body) {
  const res = await fetch('/api/sources', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
  return data
}

export async function postMaterialize(profile) {
  const res = await fetch('/api/materialize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
  return data
}

export async function toggleSource(name, enabled) {
  const res = await fetch(`/api/sources/${name}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
  return data
}

export function fetchRun(profile) {
  const base = `/api/profiles/${profile}/runs/latest`
  return Promise.all([
    getJson(`${base}/report`),
    getJson(`${base}/cells`),
    getJson(`${base}/curves`),
    getJson(`${base}/importance`),
  ]).then(([report, cells, curves, importance]) => ({ report, cells, curves, importance }))
}
