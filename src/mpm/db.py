"""Optional Postgres catalog.

The split of responsibilities is deliberate and production-shaped:

* **Postgres is the system of record for the catalog** -- which sources exist
  (including user-added ones), which runs have been published, and the
  per-cell scores in queryable form.
* **The volume keeps the bulk artifacts** -- raw downloads, stage parquet,
  and the pre-built JSON payloads the frontend streams. Nobody wants 40k-row
  map payloads assembled from SQL on every page load.

Everything here degrades gracefully: no ``DATABASE_URL`` means every reader
returns its file-mode fallback and every writer is a no-op, so the pipeline
and API still work as a pure-filesystem system (and the test suite does not
need a database to pass).
"""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

from mpm.etl.sources import REGISTRY, Source

log = logging.getLogger("mpm.db")

EXTRA_KINDS = ("xyz_grid", "ngdb_geochem")
"""Source kinds a user may add at runtime.

* ``xyz_grid``     -- headerless lon/lat/value triples, optionally gzipped
                      (the format of the USGS magnetic and gravity grids).
                      ``value_name`` names the covariate column.
* ``ngdb_geochem`` -- an NGDB-style csv zip (main.csv + bestvalue.csv), like
                      the soil or heavy-mineral-concentrate media on mrdata.
                      ``value_name`` prefixes the element columns.
"""

# Sources are fetched by the pipeline, so an open POST endpoint would be an
# SSRF invitation. Default to the host every built-in lives on; widen
# deliberately via env when needed.
DEFAULT_ALLOWED_HOSTS = ("mrdata.usgs.gov",)

PROTECTED_SOURCES = ("mrds", "magnetic")
"""Cannot be disabled: MRDS supplies the labels and the magnetic grid defines
the cube's spatial domain."""

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE TABLE IF NOT EXISTS catalog.sources (
    name        text PRIMARY KEY,
    url         text NOT NULL,
    filename    text NOT NULL,
    kind        text NOT NULL,
    value_name  text,
    description text NOT NULL DEFAULT '',
    citation    text NOT NULL DEFAULT '',
    enabled     boolean NOT NULL DEFAULT true,
    builtin     boolean NOT NULL DEFAULT false,
    sha256_pin  text,
    added_utc   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS catalog.runs (
    run_id      text PRIMARY KEY,
    profile     text NOT NULL,
    created_utc timestamptz NOT NULL DEFAULT now(),
    headline    jsonb NOT NULL DEFAULT '{}',
    report      jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS catalog.run_cells (
    run_id    text NOT NULL REFERENCES catalog.runs(run_id) ON DELETE CASCADE,
    model     text NOT NULL,
    h3        text NOT NULL,
    score     double precision,
    score_std double precision,
    label     integer,
    fold      integer,
    PRIMARY KEY (run_id, model, h3)
);
CREATE INDEX IF NOT EXISTS run_cells_score_idx ON catalog.run_cells (run_id, model, score DESC);
"""


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or None


def available() -> bool:
    if not database_url():
        return False
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return True


@contextmanager
def connect():
    import psycopg

    with psycopg.connect(database_url()) as conn:
        yield conn


def init_schema() -> None:
    """Create tables and seed the built-in sources. Safe to call every start."""
    with connect() as conn:
        conn.execute(_SCHEMA)
        for s in REGISTRY.values():
            conn.execute(
                """
                INSERT INTO catalog.sources (name, url, filename, kind, description, citation,
                                     builtin, sha256_pin)
                VALUES (%s, %s, %s, 'builtin', %s, %s, true, %s)
                ON CONFLICT (name) DO NOTHING
                """,
                (s.name, s.url, s.filename, s.description, s.citation, s.sha256),
            )


# --------------------------------------------------------------------- sources


def allowed_hosts() -> tuple[str, ...]:
    env = os.environ.get("MPM_SOURCE_HOSTS")
    return tuple(h.strip() for h in env.split(",")) if env else DEFAULT_ALLOWED_HOSTS


class SourceValidationError(ValueError):
    pass


def validate_new_source(name: str, url: str, kind: str, value_name: str) -> None:
    if not _NAME_RE.match(name):
        raise SourceValidationError(
            f"name {name!r} must be lowercase [a-z0-9_], 3-41 chars, starting with a letter"
        )
    if kind not in EXTRA_KINDS:
        raise SourceValidationError(f"kind must be one of {EXTRA_KINDS}")
    if not _NAME_RE.match(value_name):
        raise SourceValidationError("value_name must be a lowercase identifier")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SourceValidationError("url must be https")
    if parsed.hostname not in allowed_hosts():
        raise SourceValidationError(
            f"host {parsed.hostname!r} is not allowlisted; the pipeline fetches these "
            f"URLs, so hosts are restricted to {allowed_hosts()} "
            "(override with MPM_SOURCE_HOSTS)"
        )


def add_source(
    name: str, url: str, kind: str, value_name: str,
    description: str = "", citation: str = "",
) -> dict[str, Any]:
    validate_new_source(name, url, kind, value_name)
    filename = f"{name}--{os.path.basename(urlparse(url).path)}"
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO catalog.sources (name, url, filename, kind, value_name, description, citation)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
            RETURNING name
            """,
            (name, url, filename, kind, value_name, description, citation),
        )
        if cur.fetchone() is None:
            raise SourceValidationError(f"source {name!r} already exists")
    return {"name": name, "url": url, "kind": kind, "value_name": value_name}


def set_source_enabled(name: str, enabled: bool) -> None:
    if name in PROTECTED_SOURCES and not enabled:
        raise SourceValidationError(
            f"{name!r} cannot be disabled: it supplies the labels or the spatial domain"
        )
    with connect() as conn:
        cur = conn.execute(
            "UPDATE catalog.sources SET enabled = %s WHERE name = %s RETURNING name", (enabled, name)
        )
        if cur.fetchone() is None:
            raise KeyError(name)


def list_sources() -> list[dict[str, Any]]:
    """All cataloged sources. File-mode fallback: the built-in registry."""
    if not available():
        return [
            {
                "name": s.name, "url": s.url, "kind": "builtin", "value_name": None,
                "description": s.description, "citation": s.citation,
                "enabled": True, "builtin": True,
            }
            for s in REGISTRY.values()
        ]
    init_schema()
    with connect() as conn:
        cur = conn.execute(
            """
            SELECT name, url, kind, value_name, description, citation, enabled, builtin
            FROM catalog.sources ORDER BY builtin DESC, added_utc, name
            """
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def extra_sources() -> list[tuple[Source, str, str]]:
    """Enabled user-added sources, as (Source, kind, value_name) triples for
    the pipeline. Empty without a database -- extras are a catalog feature."""
    if not available():
        return []
    init_schema()
    with connect() as conn:
        cur = conn.execute(
            """
            SELECT name, url, filename, kind, value_name, description, citation, sha256_pin
            FROM catalog.sources WHERE NOT builtin AND enabled ORDER BY name
            """
        )
        rows = cur.fetchall()
    return [
        (
            Source(
                name=r[0], url=r[1], filename=r[2],
                description=r[5] or "", citation=r[6] or "", sha256=r[7],
            ),
            r[3],
            r[4],
        )
        for r in rows
    ]


def builtin_enabled(name: str) -> bool:
    """Whether a built-in source is enabled in the catalog (default true)."""
    if not available():
        return True
    with connect() as conn:
        cur = conn.execute("SELECT enabled FROM catalog.sources WHERE name = %s", (name,))
        row = cur.fetchone()
    return True if row is None else bool(row[0])


# ------------------------------------------------------------------------ runs


def record_run(run_id: str, profile: str, headline: dict, report: dict) -> None:
    if not available():
        return
    init_schema()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO catalog.runs (run_id, profile, headline, report)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (run_id) DO NOTHING
            """,
            (run_id, profile, json.dumps(headline, default=float),
             json.dumps(report, default=float)),
        )
    log.info("recorded run %s in catalog", run_id)


def copy_cells(run_id: str, scores_by_model: dict[str, Any]) -> None:
    """Bulk-load per-cell scores via COPY. ``scores_by_model`` maps model name
    to the RunResult.scores DataFrame."""
    if not available():
        return
    import numpy as np

    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM catalog.run_cells WHERE run_id = %s LIMIT 1", (run_id,)
        ).fetchone()
        if exists:
            return
        with conn.cursor() as cur, cur.copy(
            "COPY catalog.run_cells (run_id, model, h3, score, score_std, label, fold) FROM STDIN"
        ) as cp:
            for model, scores in scores_by_model.items():
                for h3, score, std, label, fold in zip(
                    scores["h3"], scores["score"], scores["score_std"],
                    scores["deposit"], scores["fold"],
                ):
                    cp.write_row((
                        run_id, model, h3,
                        None if np.isnan(score) else float(score),
                        None if np.isnan(std) else float(std),
                        int(label), int(fold),
                    ))
    log.info("loaded %d models' cell scores for %s", len(scores_by_model), run_id)


def list_runs(profile: str | None = None) -> list[dict[str, Any]]:
    if not available():
        return []
    init_schema()
    with connect() as conn:
        q = "SELECT run_id, profile, created_utc, headline FROM catalog.runs"
        params: tuple = ()
        if profile:
            q += " WHERE profile = %s"
            params = (profile,)
        cur = conn.execute(q + " ORDER BY created_utc DESC", params)
        return [
            {"run_id": r[0], "profile": r[1], "created_utc": str(r[2]), "headline": r[3]}
            for r in cur.fetchall()
        ]
