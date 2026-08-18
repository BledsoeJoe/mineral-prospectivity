// Thin client for the read-only artifacts API. Everything is origin-relative:
// vite proxies /api in dev, nginx proxies it in the compose stack.

async function getJson(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`)
  return res.json()
}

export const fetchProfiles = () => getJson('/api/profiles')

export function fetchRun(profile) {
  const base = `/api/profiles/${profile}/runs/latest`
  return Promise.all([
    getJson(`${base}/report`),
    getJson(`${base}/cells`),
    getJson(`${base}/curves`),
    getJson(`${base}/importance`),
  ]).then(([report, cells, curves, importance]) => ({ report, cells, curves, importance }))
}
