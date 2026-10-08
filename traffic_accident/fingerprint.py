"""Content identity and source-change checks for inspectable local artifacts."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .errors import SetupError


@dataclass(frozen=True)
class SourceFingerprint:
    sha256: str
    size_bytes: int
    mtime_ns: int

    def verify_unchanged(self, path: Path) -> None:
        try:
            current = path.stat()
        except OSError as exc:
            raise SetupError(f"Source became unavailable while processing: {exc}.") from exc
        if current.st_size != self.size_bytes or current.st_mtime_ns != self.mtime_ns:
            raise SetupError("Source video changed while processing. Retry with a stable local file; no incident bundle was published.")


def fingerprint_source(path: Path) -> SourceFingerprint:
    """Hash in streaming chunks, without retaining video bytes in RAM."""
    try:
        before = path.stat()
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        fingerprint = SourceFingerprint(digest, before.st_size, before.st_mtime_ns)
        fingerprint.verify_unchanged(path)
        return fingerprint
    except OSError as exc:
        raise SetupError(f"Cannot fingerprint the local video: {exc}.") from exc
