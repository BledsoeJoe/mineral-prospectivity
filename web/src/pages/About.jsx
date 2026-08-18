import React from 'react'

export default function About() {
  return (
    <div className="prose-page">
      <div className="prose">
        <h1>About this project</h1>
        <p className="lede">
          An exploratory, heavily AI-assisted study of how machine learning
          meets mineral exploration data — built to learn the domain, and to
          show how I work.
        </p>

        <h2>What this is</h2>
        <p>
          I built this while preparing an application to KoBold Metals, to
          understand the problems their data teams actually work on before
          claiming I could contribute to them. It is an end-to-end system: a
          structured ingest pipeline that acquires and cleans open USGS
          datasets, a positive-unlabeled model that ranks ground by copper
          prospectivity, an honest evaluation harness, and this app to make
          the results legible.
        </p>
        <p>
          The centre of gravity is deliberately <i>not</i> the model — it is
          gradient-boosted trees, the most boring reasonable choice. The work
          is in everything around it: where the data comes from, what has to
          be true for a validation number to mean anything, and the three ways
          this task quietly produces impressive-looking garbage (covered on
          the <a href="/breakdown">Breakdown</a> page).
        </p>

        <h2>The AI-assistance disclosure, plainly</h2>
        <p>
          This project was built in a few working sessions with heavy
          assistance from an AI coding agent (Anthropic&apos;s Claude). That
          includes most of the line-by-line code, much of the research
          synthesis, and the first drafts of prose like this page. I am not
          presenting it as artisanal hand-written software, because it
          isn&apos;t, and pretending otherwise would undermine the honesty the
          project is named for.
        </p>
        <p>
          What I <i>am</i> presenting: the direction, the judgment calls, and
          the verification. Which datasets to use and what a defensible label
          definition is; whether a covariate belongs on the geology side or the
          effort side of the audit; whether a validation number is believable
          or a leak (one release of this pipeline scored a perfect PR-AUC of
          1.000 — catching <i>why</i> was the actual work); when the AI&apos;s
          confident first draft was wrong. Working effectively with these tools
          — steering them toward production-shaped software, auditing what they
          produce, knowing what to distrust — is, I think, now part of the
          craft, and this project is an honest sample of mine.
        </p>

        <h2>What this is not</h2>
        <ul>
          <li>
            <b>Not original research.</b> Every idea here is borrowed from the
            literature (see <a href="/research">Research</a>) and applied to
            open data. The contribution, such as it is, is engineering and
            synthesis.
          </li>
          <li>
            <b>Not a targeting tool.</b> Cell-level scores over ~250 km²
            hexagons, surface and lithospheric-scale covariates only, no 3D
            geology, no grade or tonnage. Nobody should drill anything based
            on this map, and the uncertainty layer exists to underline that.
          </li>
          <li>
            <b>Not affiliated with KoBold or the USGS.</b> All data is public
            domain USGS/NRCan open data; all mistakes are mine.
          </li>
        </ul>

        <h2>What I&apos;d build next</h2>
        <ul>
          <li>
            Grade-tonnage weighting: a world-class orebody and a one-adit
            past producer currently count the same in the capture metric.
          </li>
          <li>
            Raster ingestion (ESRI grids, GeoTIFF) so the radiometric and
            filtered-magnetic products join the cube, not just XYZ exports.
          </li>
          <li>
            Strike-aware CV buffering — geological continuity follows
            structure, not circles.
          </li>
          <li>
            A proper labelling review with a geologist. The MRDS
            <code> prod_size ∈ {'{'}L, M{'}'}</code> filter is a defensible
            proxy for &quot;real orebody&quot;, but it is a proxy, and it is
            exactly the kind of decision that should not be made by a software
            engineer alone.
          </li>
        </ul>

        <h2>Colophon</h2>
        <p>
          Python, pandas, LightGBM, H3, FastAPI, Dagster, Postgres, React,
          deck.gl, Docker Compose. Source layout and run instructions are in
          the repository README; the <a href="/architecture">Architecture</a>
          {' '}page covers how the pieces fit.
        </p>
      </div>
    </div>
  )
}
