import React, { useEffect, useState } from 'react'
import { fetchSources, postMaterialize, postSource, toggleSource } from '../api.js'

const USGS_PROFILES = ['us-cu', 'us-znpb']

function SourcesManager() {
  const [data, setData] = useState(null)
  const [form, setForm] = useState({ name: '', url: '', kind: 'xyz_grid', value_name: '', description: '' })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null) // {ok, text}
  const [justAdded, setJustAdded] = useState(false)
  const [profile, setProfile] = useState(USGS_PROFILES[0])
  const [launch, setLaunch] = useState(null) // {state, text, url}

  const reload = () => fetchSources().then(setData).catch(() => setData({ error: true }))
  useEffect(() => { reload() }, [])

  if (!data) return <p className="form-note">Loading source catalog…</p>
  if (data.error) return <p className="form-error">Could not reach the API.</p>

  const dbUp = data.catalog_db

  const addBody = async (body) => {
    setBusy(true)
    setMsg(null)
    try {
      const res = await postSource(body)
      setMsg({ ok: true, text: `Added ${res.name} to the catalog.` })
      setJustAdded(true)
      reload()
    } catch (err) {
      setMsg({ ok: false, text: err.message })
    } finally {
      setBusy(false)
    }
  }

  const submit = async (e) => {
    e.preventDefault()
    await addBody(form)
    setForm({ name: '', url: '', kind: 'xyz_grid', value_name: '', description: '' })
  }

  const materialize = async () => {
    setLaunch({ state: 'launching', text: `Launching ${profile}…` })
    try {
      const res = await postMaterialize(profile)
      setLaunch({
        state: 'running',
        text: `Run ${res.run_id.slice(0, 8)} launched for ${profile}.`,
        url: res.watch_url,
      })
      setJustAdded(false)
    } catch (err) {
      setLaunch({ state: 'error', text: err.message })
    }
  }

  const toggle = async (s) => {
    try {
      await toggleSource(s.name, !s.enabled)
      reload()
    } catch (err) {
      setMsg({ ok: false, text: err.message })
    }
  }

  return (
    <>
      {!dbUp && (
        <p className="form-note">
          The catalog database is not connected; showing the built-in registry
          read-only. Start the <code>postgres</code> service to add or toggle
          sources.
        </p>
      )}
      <table className="sources-table">
        <thead>
          <tr><th>Source</th><th>Kind</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          {data.sources.map((s) => (
            <tr key={s.name}>
              <td>
                <b>{s.name}</b>
                <div className="form-note" style={{ maxWidth: 420 }}>
                  <a href={s.url} target="_blank" rel="noreferrer">{s.url}</a>
                </div>
              </td>
              <td>
                {s.kind}
                {s.value_name && <div className="form-note">→ {s.value_name}</div>}
              </td>
              <td>
                <span className={`badge ${s.enabled ? 'on' : ''}`}>
                  {s.enabled ? 'enabled' : 'disabled'}
                </span>
                {s.builtin && <span className="badge" style={{ marginLeft: 4 }}>builtin</span>}
              </td>
              <td>
                <button
                  className="toggle-btn"
                  disabled={!dbUp || ['mrds', 'magnetic'].includes(s.name)}
                  title={['mrds', 'magnetic'].includes(s.name)
                    ? 'supplies the labels / spatial domain; cannot be disabled'
                    : ''}
                  onClick={() => toggle(s)}
                >
                  {s.enabled ? 'disable' : 'enable'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Add a source</h3>
      {data.suggestions.length > 0 ? (
        <>
          <p className="form-note">
            Curated additions, each verified to download and parse against the
            current readers:
          </p>
          {data.suggestions.map((s) => (
            <div className="paper" key={s.name}>
              <div className="p-title">
                {s.name}
                <span className="badge" style={{ marginLeft: 8 }}>{s.kind}</span>
                <span className="form-note" style={{ marginLeft: 8 }}>{s.size_mb} MB</span>
              </div>
              <div className="p-venue">
                <a href={s.url} target="_blank" rel="noreferrer">{s.url}</a>
              </div>
              <div className="p-how">{s.description}</div>
              <div style={{ marginTop: 10 }}>
                <button
                  className="primary-btn"
                  disabled={!dbUp || busy}
                  onClick={() => addBody({
                    name: s.name, url: s.url, kind: s.kind,
                    value_name: s.value_name, description: s.description,
                    citation: s.citation,
                  })}
                >
                  {busy ? 'Adding…' : 'Add to catalog'}
                </button>
              </div>
            </div>
          ))}
        </>
      ) : (
        <p className="form-note">
          Every curated source is already in the catalog. Custom sources can
          still be registered below.
        </p>
      )}
      {msg && <p className={msg.ok ? 'form-ok' : 'form-error'}>{msg.text}</p>}

      <details style={{ marginTop: 12 }}>
        <summary className="form-note" style={{ cursor: 'pointer' }}>
          Advanced: register a custom source by URL
        </summary>
        <p className="form-note" style={{ marginTop: 8 }}>
          Two kinds are understood: <code>xyz_grid</code> (headerless
          lon/lat/value triples, optionally gzipped, the format of the USGS
          magnetic and gravity grids) and <code>ngdb_geochem</code> (an
          NGDB-style csv zip). Hosts are restricted to{' '}
          {data.allowed_hosts.join(', ')}; the pipeline fetches these URLs, so
          this is not an open proxy.
        </p>
        <form className="source-form" onSubmit={submit}>
          <label>
            name
            <input value={form.name} required pattern="[a-z][a-z0-9_]{2,40}"
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label>
            kind
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {data.kinds.map((k) => <option key={k}>{k}</option>)}
            </select>
          </label>
          <label className="full">
            url
            <input value={form.url} required type="url"
              onChange={(e) => setForm({ ...form, url: e.target.value })} />
          </label>
          <label>
            {form.kind === 'xyz_grid' ? 'covariate name' : 'column prefix'}
            <input value={form.value_name} required pattern="[a-z][a-z0-9_]{2,40}"
              onChange={(e) => setForm({ ...form, value_name: e.target.value })} />
          </label>
          <label>
            description
            <input value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
          <div className="actions">
            <button className="primary-btn" disabled={!dbUp || busy} type="submit">
              {busy ? 'Adding…' : 'Add source'}
            </button>
          </div>
        </form>
        <p className="form-note">
          Naming is not cosmetic: the covariate name decides which side of the
          exploration-bias audit a source lands on. Name a column
          <code> sample_density_soil</code> and the quarantine list routes it to
          the effort model; name it <code>soil</code> and its element columns
          join the geology model. Whoever adds a source makes that call,
          visibly.
        </p>
      </details>

      <h3>Rebuild the cube</h3>
      <p className="form-note">
        {justAdded
          ? 'The catalog changed. Materialize to fold the new source into the next cube:'
          : 'Launches a real Dagster run (visible in its UI, with lineage and '
            + 'logs). Stage manifests keep it cheap when nothing changed.'}
      </p>
      <div className="actions" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <select
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
          style={{
            font: 'inherit', fontSize: 13, padding: '6px 8px', borderRadius: 6,
            border: '1px solid var(--border)', background: 'var(--page)',
            color: 'var(--text-primary)',
          }}
        >
          {USGS_PROFILES.map((p) => <option key={p}>{p}</option>)}
        </select>
        <button
          className="primary-btn"
          disabled={launch?.state === 'launching'}
          onClick={materialize}
        >
          {launch?.state === 'launching' ? 'Launching…' : 'Materialize in Dagster'}
        </button>
        {launch && launch.state !== 'launching' && (
          <span className={launch.state === 'error' ? 'form-error' : 'form-ok'}>
            {launch.text}
            {launch.url && (
              <>
                {' '}
                <a href={launch.url} target="_blank" rel="noreferrer">
                  watch the run ↗
                </a>
              </>
            )}
          </span>
        )}
      </div>
    </>
  )
}

export default function Architecture() {
  return (
    <div className="prose-page">
      <div className="prose">
        <h1>Architecture</h1>
        <p className="lede">
          How raw government downloads become a scored map: a staged ingest
          pipeline with content-addressed caching, orchestrated by Dagster,
          cataloged in Postgres, served as static artifacts.
        </p>

        <h2>The services</h2>
        <div className="diagram">
          <div className="box"><div className="b-title">postgres</div><div className="b-sub">source catalog · run index · cell scores · Dagster storage</div></div>
          <div className="arrow">⇄</div>
          <div className="box"><div className="b-title">dagster</div><div className="b-sub">asset graph, partitions, schedule · /dagster/</div></div>
          <div className="arrow">→</div>
          <div className="box"><div className="b-title">./data volume</div><div className="b-sub">raw cache · stage parquet · published runs</div></div>
          <div className="arrow">→</div>
          <div className="box"><div className="b-title">api</div><div className="b-sub">FastAPI, read-only over artifacts + catalog</div></div>
          <div className="arrow">→</div>
          <div className="box"><div className="b-title">web</div><div className="b-sub">nginx + this React app · 127.0.0.1:8080</div></div>
        </div>
        <p>
          One <code>docker compose up</code> starts everything. A one-shot{' '}
          <code>pipeline</code> container also runs on startup so a fresh
          checkout produces a working map with zero clicks; after that, the
          Dagster UI is the operational surface.
        </p>

        <h2>The pipeline</h2>
        <pre>{`acquire ──┬─ labels ────┐            per-stage manifest.json:
          ├─ geophys ───┤              inputs (sha256 of upstream files)
          ├─ geochem ───┼─ cube ──►    config fingerprint
          └─ extras ────┘   │          outputs (path, sha256, rows)
                            ▼          duration, QA results
                     train + publish`}</pre>
        <table>
          <thead><tr><th>Stage</th><th>What it does</th><th>The part that bites</th></tr></thead>
          <tbody>
            <tr>
              <td><b>acquire</b></td>
              <td>Streaming downloads into a local cache</td>
              <td>
                Atomic writes (temp + rename), retry with backoff, sha256
                recorded while streaming, optional pinning. A killed download
                can never masquerade as a complete file.
              </td>
            </tr>
            <tr>
              <td><b>labels</b></td>
              <td>MRDS records → deposit cells + effort covariates</td>
              <td>
                Free-text commodity fields, Null Island coordinates, duplicate
                sites under three names, and the label-definition decisions
                (status, production size) that move results more than any
                model choice.
              </td>
            </tr>
            <tr>
              <td><b>geophys</b></td>
              <td>Magnetic + gravity XYZ grids → per-cell mean and roughness</td>
              <td>~10M headerless triples; the magnetic grid defines the cube&apos;s domain.</td>
            </tr>
            <tr>
              <td><b>geochem</b></td>
              <td>NGDB stream sediments → per-cell log-median chemistry</td>
              <td>
                Censored values encoded as <i>negative detection limits</i>
                (substituted DL/2), mixed ppm/pct units, 40% of measurements
                censored.
              </td>
            </tr>
            <tr>
              <td><b>extras</b></td>
              <td>User-registered sources from the catalog (below)</td>
              <td>Same validation gates as the built-ins; no code change to add one.</td>
            </tr>
            <tr>
              <td><b>cube</b></td>
              <td>Join everything on H3; attach labels; leave-self-out distance features</td>
              <td>
                Zero-vs-missing semantics per column (no geochem sample = an
                effort fact, not a gap), and the label-leak trap this stage
                once shipped.
              </td>
            </tr>
            <tr>
              <td><b>train + publish</b></td>
              <td>Spatial-CV PU experiment → immutable run directory + catalog row</td>
              <td>
                Every run is self-describing: config, metrics, audits and full
                provenance in one <code>report.json</code>.
              </td>
            </tr>
          </tbody>
        </table>

        <h3>Freshness without a scheduler</h3>
        <p>
          Every stage writes a manifest fingerprinting everything that could
          change its result: upstream file hashes, the relevant slice of
          profile config, a stage code version. Re-running the pipeline
          recomputes exactly the stages whose fingerprints changed: a changed
          upstream file invalidates its dependents automatically, an unchanged
          pipeline is a fast no-op, and the whole data directory can move
          between host and container (paths are stored stage-relative). It is
          a file-based, greppable take on content-addressed caching.
        </p>

        <h2>Dagster</h2>
        <p>
          The stage functions are orchestrator-free; Dagster wraps each one as
          an <b>asset</b> and contributes what a bare CLI cannot: the lineage
          graph, partitioning (one partition per commodity profile:{' '}
          <code>us-cu</code>, <code>us-znpb</code>), run history, retries, an
          operational UI, and a weekly refresh schedule (off by default;
          USGS revises these compilations in place, and the manifests make a
          re-check cheap).
        </p>
        <p>
          The two caching layers compose rather than fight: Dagster decides{' '}
          <i>when</i> a step runs; the stage manifest decides whether the step
          has any work to do. &quot;Materialize all&quot; is therefore always
          safe. Instance storage (run history, event logs) lives in the same
          Postgres as the catalog, so CLI materializations and the webserver
          share one history. The UI is served by this app&apos;s own nginx at{' '}
          <a href="/dagster/" target="_blank" rel="noreferrer">/dagster/ ↗</a>,
          so it lives on the same origin and behind the same access control as
          everything else; no separate port is exposed.
        </p>

        <h2>Postgres: the catalog</h2>
        <table>
          <thead><tr><th>Table</th><th>Holds</th></tr></thead>
          <tbody>
            <tr>
              <td><code>sources</code></td>
              <td>
                The data-source registry: the five built-ins plus anything
                registered below. Enable/disable state gates what the next
                cube build includes.
              </td>
            </tr>
            <tr>
              <td><code>runs</code></td>
              <td>Every published run: profile, timestamps, headline metrics, full report (jsonb).</td>
            </tr>
            <tr>
              <td><code>run_cells</code></td>
              <td>
                Per-cell scores for all three models, COPY-loaded: the
                SQL-queryable view of every map ever published
                (<code>select … from run_cells where run_id = … order by score desc</code>).
              </td>
            </tr>
          </tbody>
        </table>
        <p>
          The split of responsibilities is deliberate: Postgres is the system
          of record for the <i>catalog</i>; bulk artifacts (raw downloads,
          stage parquet, the pre-gzipped map payloads) stay on the volume. The
          catalog can always be rebuilt from the artifacts
          (<code>mpm etl backfill-db</code>), never the other way around.
          Without a database the whole system degrades gracefully to
          file-only mode.
        </p>

        <h2 id="sources">Data sources</h2>
        <SourcesManager />
      </div>
    </div>
  )
}
