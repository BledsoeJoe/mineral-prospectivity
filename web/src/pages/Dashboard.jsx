import React, { useEffect, useState } from 'react'
import { fetchProfiles, fetchRun } from '../api.js'
import MapView from '../components/MapView.jsx'
import KpiRow from '../components/KpiRow.jsx'
import CaptureChart from '../components/CaptureChart.jsx'
import ImportanceChart from '../components/ImportanceChart.jsx'
import { OptimismCard, BiasCard, DiscoveryCard, ProvenanceCard } from '../components/Audits.jsx'

export default function Dashboard() {
  const [profiles, setProfiles] = useState(null)
  const [profile, setProfile] = useState(null)
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchProfiles()
      .then((ps) => {
        setProfiles(ps)
        if (ps.length) {
          const preferred = ps.find((p) => p.profile !== 'synth') ?? ps[0]
          setProfile(preferred.profile)
        }
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!profile) return
    setRun(null)
    fetchRun(profile)
      .then(setRun)
      .catch((e) => setError(e.message))
  }, [profile])

  if (error) {
    return (
      <div className="error-box">
        <div>
          <p><b>Could not load results.</b></p>
          <p>{error}</p>
          <p>
            Has the pipeline published a run yet? Try:{' '}
            <code>python -m mpm etl run --profile synth</code>
          </p>
        </div>
      </div>
    )
  }
  if (!profiles || (profile && !run)) return <div className="loading">Loading…</div>
  if (!profiles.length) {
    return (
      <div className="error-box">
        <div>
          <p><b>No published runs found.</b></p>
          <p>Run the pipeline first: <code>python -m mpm etl run --profile synth</code></p>
        </div>
      </div>
    )
  }

  const { report, cells, curves, importance } = run
  const hasTruth = Boolean(report.discovery_audit)

  return (
    <div className="page-dashboard">
      <div className="subbar">
        <select value={profile} onChange={(e) => setProfile(e.target.value)}>
          {profiles.map((p) => (
            <option key={p.profile} value={p.profile}>{p.profile}</option>
          ))}
        </select>
        <span className="sub">{report.profile?.description ?? ''}</span>
        <span className="run-id">run {report.run_id}</span>
      </div>
      <div className="layout">
        <MapView cells={cells} hasTruth={hasTruth} />
        <aside className="side-pane">
          <KpiRow report={report} />
          <CaptureChart curves={curves} />
          <BiasCard audit={report.bias_audit} />
          <OptimismCard optimism={report.optimism} />
          {hasTruth && <DiscoveryCard audit={report.discovery_audit} />}
          <ImportanceChart importance={importance} />
          <ProvenanceCard report={report} />
        </aside>
      </div>
    </div>
  )
}
