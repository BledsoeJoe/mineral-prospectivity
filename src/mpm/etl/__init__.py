"""Structured ingest pipeline: acquire -> validate -> grid -> assemble -> train -> publish.

The model code in :mod:`mpm` answers "is this map honest?". This package answers
the operational question that comes before it: *where did the cube come from,
and can I rebuild it?* Every stage writes a manifest recording exactly what it
read, what it produced, and under which configuration -- so a run is
reproducible from (profile, raw files) alone, stale stages recompute themselves,
and fresh stages are skipped instead of re-run.

Deliberately no package-level re-exports: importing any submodule runs this
file first, and the API imports the dependency-light ``sources`` and
``profiles`` modules on an image without the model stack.
"""
