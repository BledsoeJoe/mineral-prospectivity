"""Storage seam tests, aimed at the ways it can fail quietly.

The S3 backend stands in as fsspec's in-memory filesystem, which shares the
object-store semantics that break things: no directories, no rename, listings
that include the prefix. Credential and endpoint routing need a real bucket
and are not covered here.
"""

from __future__ import annotations

import gzip
import json

import pytest

from mpm import storage
from mpm.storage import Storage, gzip_text_writer, open_storage, runs_storage


@pytest.fixture()
def objectstore() -> Storage:
    """A Storage backed by fsspec's memory filesystem, standing in for S3."""
    import fsspec

    fs = fsspec.filesystem("memory")
    fs.store.clear()
    fs.pseudo_dirs.clear()
    return Storage(fs, "/bucket/prefix", "memory")


def test_traversal_cannot_escape_the_root(tmp_path):
    """Second line of defense behind the API's name validation."""
    root = open_storage(tmp_path)
    with pytest.raises(ValueError):
        root / ".." / "etc"
    with pytest.raises(ValueError):
        root / "runs/../../secrets"
    # A leading slash must not reset to the filesystem root either.
    assert (root / "/runs").path == f"{root.path}/runs"


def test_local_write_is_atomic_and_leaves_no_temp(tmp_path):
    """A reader must never see a half-written artifact, or a stray .tmp."""
    target = open_storage(tmp_path) / "latest.json"
    target.write_text(json.dumps({"run_id": "a"}))
    target.write_text(json.dumps({"run_id": "b"}))

    assert target.read_json() == {"run_id": "b"}
    assert [p.name for p in open_storage(tmp_path).ls()] == ["latest.json"]


def test_iter_chunks_streams_rather_than_buffering(tmp_path):
    """Chunking has to be observable: a buffered read would round-trip fine."""
    payload = b"x" * (5 << 10)
    target = open_storage(tmp_path) / "cells.json.gz"
    target.write_bytes(payload)

    chunks = list(target.iter_chunks(chunk_size=1 << 10))
    assert len(chunks) == 5
    assert max(len(c) for c in chunks) == 1 << 10
    assert b"".join(chunks) == payload


def test_missing_object_raises_filenotfound(tmp_path, objectstore):
    """Both backends signal absence the same way; the API turns it into a 404."""
    for root in (open_storage(tmp_path), objectstore):
        with pytest.raises(FileNotFoundError):
            (root / "runs" / "nope" / "report.json").size()


def test_immutable_run_dir_refuses_to_overwrite(tmp_path, objectstore):
    """Without this, a republish interleaves new artifacts with old ones."""
    for root in (open_storage(tmp_path), objectstore):
        run = (root / "20260101T000000Z-abc").mkdir(exist_ok=False)
        (run / "report.json").write_text("{}")
        with pytest.raises(FileExistsError):
            (root / "20260101T000000Z-abc").mkdir(exist_ok=False)


def test_objectstore_lists_one_level_of_implicit_prefixes(objectstore):
    """Keys imply their prefixes; ``ls`` still has to behave like a directory."""
    (objectstore / "synth" / "latest.json").write_text("{}")
    (objectstore / "synth" / "run-a" / "report.json").write_text("{}")

    assert [p.name for p in objectstore.ls()] == ["synth"]
    assert [p.name for p in (objectstore / "synth").ls()] == ["latest.json", "run-a"]


def test_listing_drops_the_prefix_placeholder():
    """A placeholder key left in would invent a profile named for the root.

    Stubbed because whether a bucket has one depends on how it was created.
    """

    class _PlaceholderFS:
        def exists(self, path: str) -> bool:
            return True

        def ls(self, path: str, detail: bool = False) -> list[str]:
            return [path, f"{path}/us-cu", f"{path}/synth"]

    root = Storage(_PlaceholderFS(), "ml-data/mpm/runs", "s3")
    assert [p.name for p in root.ls()] == ["synth", "us-cu"]


def test_gzip_writer_round_trips(tmp_path):
    target = open_storage(tmp_path) / "cells.json.gz"
    with gzip_text_writer(target) as f:
        json.dump([{"h": "abc", "s": 0.5}], f, separators=(",", ":"))

    assert json.loads(gzip.decompress(target.read_bytes())) == [{"h": "abc", "s": 0.5}]


def test_uri_round_trips_through_open_storage(tmp_path):
    """published.json records a uri, so it has to reopen."""
    local = open_storage(tmp_path) / "runs" / "synth"
    assert open_storage(local.uri).path == local.path


def test_unsupported_protocol_is_refused():
    """Better a clear refusal than an untested backend that half works."""
    with pytest.raises(ValueError, match="unsupported storage protocol"):
        open_storage("gs://bucket/prefix")


def test_runs_root_env_overrides_data_root(tmp_path, monkeypatch):
    monkeypatch.delenv("MPM_RUNS_ROOT", raising=False)
    monkeypatch.setenv("MPM_DATA_ROOT", str(tmp_path))
    assert runs_storage().path == f"{tmp_path}/runs"

    monkeypatch.setenv("MPM_RUNS_ROOT", "s3://ml-data/mpm/runs")
    root = runs_storage()
    assert root.protocol == "s3" and root.uri == "s3://ml-data/mpm/runs"


def test_s3_options_carry_endpoint_but_never_credentials(monkeypatch):
    """Keys belong to botocore's chain, not to a dict handed around to pandas."""
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_ENDPOINT", "https://us-east-1.linodeobjects.com")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAnotreal")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "shhh")

    options = storage.s3_options()
    assert options["client_kwargs"]["endpoint_url"] == "https://us-east-1.linodeobjects.com"
    assert options["use_listings_cache"] is False
    assert "shhh" not in json.dumps(options)
    assert "AKIAnotreal" not in json.dumps(options)
