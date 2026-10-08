"""Atomic UTF-8/binary file publication within a same-filesystem run staging area."""

import os
from pathlib import Path
from uuid import uuid4


def atomic_write(path: Path, content: bytes) -> None:
    """Flush/fsync then replace; remove only this write's temporary file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
