import React from 'react'

export default function About() {
  return (
    <div className="prose-page">
      <div className="prose">
        <h1>About this project</h1>
        <p className="lede">
          An exploratory, AI-assisted project: turning raw public mineral
          exploration data into a published, versioned data product, and
          running the system that does it across two machines.
        </p>

        <h2>What this is</h2>
        <p>
          I built this while preparing an application to KoBold Metals, to get
          a feel for the data problems behind mineral exploration before
          claiming I could help with them. It is an end-to-end data system over
          open USGS data: a staged pipeline that downloads, validates and cleans
          deposit records, geophysics and geochemistry; a step that joins them
          onto a common hexagonal grid; a simple model that ranks that grid by
          copper prospectivity; and a publishing layer that turns each run into
          versioned files this app, a notebook, or a query engine can read.
        </p>
        <p>
          The model is deliberately the least interesting part. Most of the
          effort went into the data around it: where each file came from and
          whether it changed, the quirks of public datasets that silently
          corrupt results, how a label gets defined, and how a finished run is
          published so that people and other programs can use it. The{' '}
          <a href="/breakdown">Breakdown</a> page walks that data path.
        </p>

        <h2>From one machine to two</h2>
        <p>
          The first version ran as a single unit: pipeline, orchestrator,
          database, API and frontend on one machine, sharing one local data
          directory. That is fine for a demo and wrong for real use. The
          pipeline needs compute, credentials and privacy, and can live on a
          less reliable machine; the public site needs very little and should
          stay up regardless. Splitting it took, roughly in order:
        </p>
        <ul>
          <li>
            <b>A storage layer.</b> Published runs are read and written through
            one small interface over either a local directory or an S3 prefix,
            so the pipeline and the API work against either unchanged. Only
            finished runs move to the bucket. The download cache and stage
            bookkeeping stay on local disk, because the pipeline&apos;s
            freshness checks rely on cheap, atomic file operations that object
            storage does not offer.
          </li>
          <li>
            <b>Ordering instead of transactions.</b> S3 cannot update several
            objects atomically, so a run is written in full before the small
            pointer naming the latest run moves. A crash midway leaves an
            unreachable orphan, never a half-published run.
          </li>
          <li>
            <b>Streaming reads.</b> The API streams artifacts out of the bucket
            in bounded chunks instead of loading them, so a small server can
            serve large files.
          </li>
          <li>
            <b>A read-only public API.</b> On the public node, the routes that
            register sources or launch pipeline runs are never mounted, and the
            API refuses to start without a bucket, so a misconfigured node
            fails loudly instead of quietly serving nothing.
          </li>
          <li>
            <b>Per-node deployment.</b> Each machine has its own compose file
            and environment. The private node runs everything with a read-write
            key; the public node runs only the API and frontend with a
            read-only key, on an image about a quarter the size because it
            carries no model code.
          </li>
        </ul>
        <p>
          The bucket is now the only connection between the two machines,
          nothing on the public side can reach the private side, and if the
          pipeline machine goes down the site keeps serving the last published
          run. The <a href="/architecture">Architecture</a> page covers the
          pieces in more detail.
        </p>

        <h2>How this was built</h2>
        <p>
          This project was built with heavy assistance from an AI coding agent
          (Anthropic&apos;s Claude), and most of it was not written by me line
          by line. The first version came out of a few working sessions in
          which I set goals and constraints and the agent did nearly all of the
          implementation, along with most of the technical calls inside it:
          which datasets to use, how deposits are labeled, which covariates
          count as exploration effort, and catching and fixing its own
          label-leak bug. It also drafted most of the prose, including a first
          version of this page that overstated my part.
        </p>
        <p>
          What was mine in that first version: the purpose and scope, the shape
          of the system (a staged pipeline feeding a web app), Docker Compose as
          the deployment target, the requirements for Dagster, Postgres and
          runtime-registered data sources, the structure of these pages, and the
          deployment security fixes (binding to loopback only, and serving
          Dagster behind the app&apos;s own proxy).
        </p>
        <p>
          Since then I have treated the codebase as one I inherited: reading
          it, questioning it, and extending it in small, scoped changes. The
          move to object storage and two machines was that kind of work. I
          defined the goal and constraints, decided what the public node should
          and should not be able to do, tested the storage layer against a real
          S3-compatible bucket, and reviewed each phase before committing it;
          the agent wrote most of the code. Directing and auditing these tools
          well is, I think, now part of the job, and this is an honest sample of
          how I do it.
        </p>

        <h2>What this is not</h2>
        <ul>
          <li>
            <b>Not original research.</b> Every modeling idea here is borrowed
            from the literature (see <a href="/research">Research</a>) and
            applied to open data. The contribution, such as it is, is
            engineering.
          </li>
          <li>
            <b>Not a targeting tool.</b> Cell-level scores over ~250 km²
            hexagons, surface and lithospheric-scale covariates only, no 3D
            geology, no grade or tonnage. Nobody should drill anything based
            on this map.
          </li>
          <li>
            <b>Not affiliated with KoBold or the USGS.</b> All data is public
            domain USGS/NRCan open data; all mistakes are mine.
          </li>
        </ul>

        <h2>What I&apos;d build next</h2>
        <ul>
          <li>
            Raster ingestion (ESRI grids, GeoTIFF), so radiometric and
            filtered-magnetic products join the cube, not just XYZ exports.
          </li>
          <li>
            On-demand extracts: an area and a commodity in, a documented bundle
            of the relevant covariates and scores out, cut from published runs.
          </li>
          <li>
            A searchable catalog across sources and runs, so a geologist can
            ask what data covers an area, or what a past run used, without
            reading files.
          </li>
          <li>
            Requests from the public site without a route back: the public API
            writes a request into the bucket and the private pipeline picks it
            up.
          </li>
          <li>
            Moving stage bookkeeping and orchestrator state off local disk, so
            the pipeline machine itself becomes replaceable.
          </li>
          <li>
            A labeling review with a geologist. The MRDS
            <code> prod_size ∈ {'{'}L, M{'}'}</code> filter is a defensible
            proxy for &quot;real orebody&quot;, but it is a proxy, and exactly
            the kind of decision that should not be made by a software engineer
            alone.
          </li>
        </ul>

        <h2>Colophon</h2>
        <p>
          Python, pandas, LightGBM, H3, FastAPI, Dagster, Postgres, fsspec and
          s3fs over S3-compatible object storage, React, deck.gl, nginx, Docker
          Compose. Source layout and run instructions are in the repository
          README; the <a href="/architecture">Architecture</a> page covers how
          the pieces fit.
        </p>
      </div>
    </div>
  )
}
