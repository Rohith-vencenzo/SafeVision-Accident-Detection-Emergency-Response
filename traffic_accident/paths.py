"""Consistent root-relative CLI paths with helpful Windows/WSL diagnostics."""

import os
from pathlib import Path, PureWindowsPath

from .errors import SetupError


def resolve_cli_path(value: Path, root: Path) -> Path:
    """Resolve a local CLI path; never interpret a URL as a video source."""
    text = str(value)
    if "://" in text or text.startswith(("http:", "https:", "rtsp:")):
        raise SetupError("Only local files are supported. Download the video yourself and pass its local --source path.")
    windows = PureWindowsPath(text)
    if os.name != "nt" and windows.drive:
        suggestion = "Use the Linux/WSL path for that file."
        if len(windows.drive) == 2 and windows.drive[1] == ":":
            mapped = Path("/mnt") / windows.drive[0].lower() / Path(*windows.parts[1:])
            suggestion = f'In WSL, try "{mapped}".'
        raise SetupError(f"A Windows path was supplied to Linux Python. {suggestion}")
    path = value.expanduser()
    return (path if path.is_absolute() else root / path).resolve()
