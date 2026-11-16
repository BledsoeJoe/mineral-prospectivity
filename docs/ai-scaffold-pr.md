# AI-generated scaffold: end-to-end mineral prospectivity system

## What this is

This branch is the entire output of a few working sessions with an AI coding
agent (Anthropic's Claude, driven through Claude Code). I directed it at the
level of goals, constraints, and feature requests; the agent wrote effectively
all of the code, made most of the technical judgment calls, and verified its
own work. The commit history on this branch is the agent's work under my
direction, not mine.

Merging it is deliberate. From this point on I am treating the project as a
simulated inherited codebase: a system I did not write, arriving all at once,
that I now have to understand, question, correct, and extend. That mirrors the
actual job of a working engineer more closely than a greenfield toy would, and
it keeps the provenance honest. Everything after this merge is my work, with
any further AI-written changes arriving as their own clearly scoped branches
and reviewed like anyone else's code.

## What the scaffold contains

- A staged ingest pipeline over open USGS data (MRDS occurrences,
  aeromagnetics, gravity, NGDB geochemistry) with content-addressed stage
  manifests, checksummed cached downloads, and data-quality gates
- A positive-unlabeled LightGBM model with spatial block cross-validation and
  exploration-bias audits
- A Dagster asset graph over the same stage functions, partitioned by
  commodity profile, with Postgres backing both Dagster storage and a catalog
  of sources, runs, and per-cell scores
- A source registry that accepts new USGS datasets at runtime and folds them
  into the next cube build
- A FastAPI artifact server and a React + deck.gl app (map, capture curves,
  audits, and explanatory pages)
- Docker compose deployment publishing a single loopback port, with the
  Dagster UI proxied behind the same origin
- 60 tests aimed at silent-failure paths

## Provenance, precisely

Direction that was mine: the project's purpose and scope, the pipeline plus
web app shape, docker compose as the deployment target, the Dagster, Postgres,
and user-addable data source requirements, the page structure including the
idea of an AI disclosure, the deployment security fixes (loopback-only
binding, proxying Dagster behind the app's auth), and the writing style rules.

Calls that were the agent's: dataset selection, the deposit label definition,
the effort-covariate quarantine decisions, catching and fixing its own
label-leak bug, the architecture details, all implementation, and most of the
prose, including a first-draft AI disclosure that overstated my share of the
work. Fixing that text is the first item below.

## Work after merge

- [ ] Rewrite the AI disclosure (About page and README) in my own words to
      match the provenance above
- [ ] Adversarial review of the modeling core (splits, pu, evaluate) until I
      can defend or change every decision in it
- [ ] Same for the ingest pipeline and its label-definition choices
- [ ] First fully self-written feature branch
- [x] Targeted AI-written branches with tight scopes, reviewed before merge

🤖 Generated with [Claude Code](https://claude.com/claude-code)
