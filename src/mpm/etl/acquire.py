"""Download raw sources into a local cache, safely.

The rules, each of which exists because its absence has burned someone:

* **Stream to a temp file, then atomic rename.** A killed download can never
  masquerade as a complete file.
* **Hash while streaming.** The sha256 lands in the stage manifest, so every
  downstream artifact is traceable to the exact bytes it came from.
* **Verify pins.** If the registry pins a checksum and upstream serves
  something else, fail loudly -- silently modeling on a revised compilation
  is how two colleagues get different maps from "the same" pipeline.
* **Retry with backoff.** Government data servers flake; a pipeline that dies
  on the first 503 at 2am is not a pipeline.
* **Cache by default.** Re-running the pipeline must not re-download 200 MB.
  ``refresh=True`` is the explicit opt-in to check upstream again.
"""

from __future__ import annotations

import hashlib
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from mpm.etl.sources import Source

log = logging.getLogger("mpm.etl")

USER_AGENT = "mpm-pipeline/0.1 (mineral prospectivity research; python-urllib)"


class AcquireError(RuntimeError):
    pass


class ChecksumMismatch(AcquireError):
    pass


@dataclass(frozen=True)
class AcquiredFile:
    source: str
    path: Path
    sha256: str
    bytes: int
    from_cache: bool
    url: str

    def provenance(self) -> dict[str, object]:
        return {
            "source": self.source,
            "url": self.url,
            "path": str(self.path),
            "sha256": self.sha256,
            "bytes": self.bytes,
            "from_cache": self.from_cache,
        }


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _download(url: str, dest: Path, timeout: float, chunk: int = 1 << 20) -> str:
    """Stream ``url`` to ``dest`` atomically; return the sha256 of the bytes."""
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    h = hashlib.sha256()
    written = 0
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        while block := resp.read(chunk):
            h.update(block)
            out.write(block)
            written += len(block)
            if written % (64 << 20) < chunk:  # log every ~64 MB
                pct = f" ({written / total:.0%})" if total else ""
                log.info("  ... %d MB%s", written >> 20, pct)
        if total and written != total:
            tmp.unlink(missing_ok=True)
            raise AcquireError(f"{url}: got {written} bytes, Content-Length said {total}")
    tmp.replace(dest)
    log.info("downloaded %s: %.1f MB in %.1fs", dest.name, written / 1e6, time.monotonic() - t0)
    return h.hexdigest()


def acquire(
    source: Source,
    raw_dir: Path,
    refresh: bool = False,
    timeout: float = 120.0,
    retries: int = 3,
    backoff_s: float = 5.0,
) -> AcquiredFile:
    """Return a verified local copy of ``source``, downloading if needed."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / source.filename

    if dest.exists() and not refresh:
        sha = _sha256_of(dest)
        _check_pin(source, sha)
        log.info("cache hit  %-18s %s (%.1f MB)", source.name, dest.name, dest.stat().st_size / 1e6)
        return AcquiredFile(source.name, dest, sha, dest.stat().st_size, True, source.url)

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            log.info("fetching   %-18s %s (attempt %d)", source.name, source.url, attempt)
            sha = _download(source.url, dest, timeout=timeout)
            _check_pin(source, sha)
            return AcquiredFile(source.name, dest, sha, dest.stat().st_size, False, source.url)
        except ChecksumMismatch:
            raise  # retrying will not change upstream's content
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < retries:
                wait = backoff_s * 2 ** (attempt - 1)
                log.warning("attempt %d failed (%s); retrying in %.0fs", attempt, e, wait)
                time.sleep(wait)
    raise AcquireError(f"failed to fetch {source.url} after {retries} attempts") from last_err


def _check_pin(source: Source, sha: str) -> None:
    if source.sha256 and source.sha256 != sha:
        raise ChecksumMismatch(
            f"{source.name}: expected sha256 {source.sha256}, got {sha}. Upstream has "
            "changed (or the cached file is corrupt). Delete the cached file to accept "
            "the new content, or update the pin."
        )
