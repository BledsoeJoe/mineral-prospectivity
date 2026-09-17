"""Artifact addressing over a local directory or an S3 bucket.

Published runs are the handoff between the pipeline that writes them and the
API that serves them, which need not share a filesystem. ``Storage`` wraps an
fsspec filesystem and pins the semantics this project relies on:

* Reads stream. ``read_bytes`` is for small JSON pointers; everything the API
  serves goes through ``iter_chunks``, so memory is flat in artifact size.
* Writes are atomic per object (local temp-and-rename, S3 single PUT) and
  never across objects, so publish order carries the commit.
* S3 has no directories: ``mkdir`` is local-only, and ``exists`` on a prefix
  answers whether any key lives under it.

Only the published-runs tree is addressed this way. The raw download cache and
the stage manifests stay local, where rename atomicity and cheap stat calls are
what stage freshness is built on.

Configuration::

    MPM_RUNS_ROOT     published runs; a local path or s3://bucket/prefix
    MPM_DATA_ROOT     fallback root when MPM_RUNS_ROOT is unset (default: data)
    AWS_ENDPOINT_URL  S3-compatible endpoint (AWS_ENDPOINT also accepted)
    AWS_REGION

Credentials come from botocore's chain, never from arguments, so a reader can
run on an instance role and hold no keys.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import posixpath
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

DEFAULT_CHUNK_BYTES = 1 << 20
SUPPORTED_PROTOCOLS = ("file", "s3")


class Storage:
    """One location in an artifact store: a local path or an S3 key prefix.

    Descends with ``/`` like ``pathlib.Path``. Instances are cheap; the
    underlying fsspec filesystem is cached and shared.
    """

    def __init__(
        self,
        fs: Any,
        path: str,
        protocol: str,
        storage_options: dict[str, Any] | None = None,
    ) -> None:
        self.fs = fs
        self.path = path.rstrip("/") or "/"
        self.protocol = protocol
        self.storage_options = dict(storage_options or {})

    # ------------------------------------------------------------ addressing

    def _at(self, path: str) -> Storage:
        return Storage(self.fs, path, self.protocol, self.storage_options)

    def __truediv__(self, other: str | os.PathLike) -> Storage:
        rel = str(other).strip("/")
        if not rel:
            return self
        if any(part == ".." for part in rel.split("/")):
            raise ValueError(f"refusing to escape the storage root: {other!r}")
        return self._at(posixpath.join(self.path, rel))

    @property
    def uri(self) -> str:
        """Addressable form, for pandas and for provenance records."""
        return self.path if self.protocol == "file" else f"{self.protocol}://{self.path}"

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    @property
    def parent(self) -> Storage:
        return self._at(posixpath.dirname(self.path))

    def __repr__(self) -> str:
        return f"Storage({self.uri!r})"

    def __str__(self) -> str:
        return self.uri

    # -------------------------------------------------------------- metadata

    def exists(self) -> bool:
        return bool(self.fs.exists(self.path))

    def is_dir(self) -> bool:
        """Local: a real directory. S3: there is at least one key underneath."""
        return bool(self.fs.isdir(self.path))

    def size(self) -> int:
        """Byte count. Raises ``FileNotFoundError`` if the object is absent."""
        return int(self.fs.size(self.path))

    def ls(self) -> list[Storage]:
        """Direct children, sorted. Empty when the location does not exist."""
        if not self.fs.exists(self.path):
            return []
        children = self.fs.ls(self.path, detail=False)
        # S3 reports the prefix's own placeholder key among its children.
        return [self._at(c) for c in sorted(children) if c.rstrip("/") != self.path]

    # ------------------------------------------------------------- reading

    def open(self, mode: str = "rb", chunk_size: int | None = None) -> IO[Any]:
        """A streaming handle. The caller owns closing it."""
        return self.fs.open(self.path, mode, block_size=chunk_size or DEFAULT_CHUNK_BYTES)

    def iter_chunks(self, chunk_size: int | None = None) -> Iterator[bytes]:
        """Yield the object in bounded pieces, never holding the whole body."""
        size = chunk_size or DEFAULT_CHUNK_BYTES
        with self.fs.open(self.path, "rb", block_size=size) as f:
            while block := f.read(size):
                yield block

    def read_bytes(self) -> bytes:
        """Whole-object read. Only for artifacts known to be small."""
        return self.fs.cat_file(self.path)

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding)

    def read_json(self) -> Any:
        return json.loads(self.read_text())

    def read_parquet(self, **kwargs: Any) -> Any:
        import pandas as pd

        return pd.read_parquet(self.uri, storage_options=self.storage_options or None, **kwargs)

    # ------------------------------------------------------------- writing

    def mkdir(self, exist_ok: bool = True) -> Storage:
        """Create a local directory; a no-op on S3, where prefixes are implicit.

        ``exist_ok=False`` guards both backends, so immutable run directories
        get the same refusal either way.
        """
        if not exist_ok and self.exists():
            raise FileExistsError(self.uri)
        if self.protocol == "file":
            self.fs.makedirs(self.path, exist_ok=True)
        return self

    def write_bytes(self, data: bytes) -> Storage:
        """Write ``data`` in one shot, as atomically as the backend allows."""
        self._ensure_parent()
        if self.protocol == "file":
            tmp = self.path + ".tmp"
            self.fs.pipe_file(tmp, data)
            self.fs.mv(tmp, self.path)
        else:
            self.fs.pipe_file(self.path, data)
        return self

    def write_text(self, text: str, encoding: str = "utf-8") -> Storage:
        return self.write_bytes(text.encode(encoding))

    def writer(self) -> IO[bytes]:
        """Streaming binary writer for incrementally built artifacts.

        Not atomic: the object appears as it is uploaded. Safe inside a run
        directory, which stays unreachable until ``latest.json`` is repointed.
        """
        self._ensure_parent()
        return self.fs.open(self.path, "wb")

    def write_parquet(self, df: Any, **kwargs: Any) -> Storage:
        self._ensure_parent()
        df.to_parquet(self.uri, storage_options=self.storage_options or None, **kwargs)
        return self

    def remove(self, recursive: bool = False) -> None:
        if self.exists():
            self.fs.rm(self.path, recursive=recursive)

    def _ensure_parent(self) -> None:
        if self.protocol == "file":
            self.fs.makedirs(posixpath.dirname(self.path), exist_ok=True)


# ------------------------------------------------------------------ factories


def s3_options() -> dict[str, Any]:
    """s3fs options from the environment, credentials deliberately excluded."""
    # Listing cache off: a long-lived reader must see runs published behind it.
    options: dict[str, Any] = {"use_listings_cache": False}
    client_kwargs: dict[str, Any] = {}
    # AWS_ENDPOINT is delta-rs's spelling, kept so one .env configures both.
    endpoint = os.environ.get("AWS_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT")
    if endpoint:
        client_kwargs["endpoint_url"] = endpoint
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region:
        client_kwargs["region_name"] = region
    if client_kwargs:
        options["client_kwargs"] = client_kwargs
    return options


def open_storage(root: str | os.PathLike | Storage, **overrides: Any) -> Storage:
    """Open an artifact root from a path or an ``s3://bucket/prefix`` URI."""
    import fsspec

    if isinstance(root, Storage):
        return root

    raw = str(root)
    protocol, _, path = raw.partition("://")
    if not _:
        protocol, path = "file", str(Path(raw).expanduser().resolve())
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError(
            f"unsupported storage protocol {protocol!r} in {raw!r}; "
            f"this project has verified {' and '.join(SUPPORTED_PROTOCOLS)}"
        )

    options = {**(s3_options() if protocol == "s3" else {}), **overrides}
    fs = fsspec.filesystem(protocol, **options)
    return Storage(fs, path.rstrip("/"), protocol, options)


def runs_storage(data_root: str | os.PathLike | None = None) -> Storage:
    """Where published runs live: ``MPM_RUNS_ROOT``, else ``<data_root>/runs``."""
    configured = os.environ.get("MPM_RUNS_ROOT")
    if configured:
        return open_storage(configured)
    base = data_root if data_root is not None else os.environ.get("MPM_DATA_ROOT", "data")
    return open_storage(base) / "runs"


@contextlib.contextmanager
def gzip_text_writer(target: Storage) -> Iterator[IO[str]]:
    """Open ``target`` as a gzip-compressing text stream.

    Compresses on the way to storage rather than into a buffer, so writer
    memory is bounded by the codec window and not by the artifact.
    """
    import gzip

    with (
        target.writer() as raw,
        gzip.GzipFile(fileobj=raw, mode="wb") as gz,
        io.TextIOWrapper(gz, "utf-8") as text,
    ):
        yield text
