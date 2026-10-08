"""Explicit retention of verified, unmodified, project-owned report bundles only."""

import hashlib
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


def managed_manifest(directory: Path) -> dict[str, Any] | None:
    """Ignore symlinks, unknown/modified files, invalid markers, and staging folders."""
    if directory.is_symlink() or not directory.is_dir() or not re.fullmatch(r"run_[0-9a-f]{32}", directory.name):
        return None
    try:
        marker = directory / "COMPLETE.json"
        if marker.is_symlink():
            return None
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        expected_keys = {"managed_by", "schema_version", "run_id", "created_at_utc", "files", "directories"}
        if not isinstance(manifest, dict) or set(manifest) != expected_keys:
            return None
        if (manifest.get("managed_by") != "traffic_accident" or manifest.get("schema_version") != "1.0"
                or manifest.get("run_id") != directory.name):
            return None
        created = datetime.fromisoformat(manifest["created_at_utc"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            return None
        files = manifest["files"]
        directories = manifest["directories"]
        if not isinstance(files, dict) or not {"result.json", "report.md"} <= files.keys() or not isinstance(directories, list):
            return None
        entries = list(directory.rglob("*"))
        if any(entry.is_symlink() for entry in entries):
            return None
        actual_files = {entry.relative_to(directory).as_posix() for entry in entries if entry.is_file()}
        actual_dirs = {entry.relative_to(directory).as_posix() for entry in entries if entry.is_dir()}
        if actual_files != set(files) | {"COMPLETE.json"} or actual_dirs != set(directories):
            return None
        for relative, digest in files.items():
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[0-9a-f]{64}", digest):
                return None
            with (directory / relative).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    return None
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        if (not isinstance(result, dict) or result.get("run_id") != directory.name
                or result.get("schema_version") != "1.0" or result.get("created_at_utc") != manifest["created_at_utc"]):
            return None
        return manifest
    except (OSError, ValueError, KeyError, TypeError):
        return None


def apply_retention(root: Path, *, keep_run_id: str, max_runs: int | None, days: float | None,
                    protected_paths: tuple[Path, ...] = (), now: datetime | None = None) -> dict[str, int]:
    """Best-effort cleanup after publication, preserving current/input/unmanaged work."""
    if max_runs is not None and (type(max_runs) is not int or max_runs < 1):
        raise ValueError("Retention max_runs must be null or a positive integer.")
    if days is not None and (isinstance(days, bool) or not isinstance(days, (int, float)) or not math.isfinite(days) or days <= 0):
        raise ValueError("Retention days must be null or positive finite days.")
    if not re.fullmatch(r"run_[0-9a-f]{32}", keep_run_id):
        raise ValueError("Retention requires the current valid run identity.")
    summary = {"removed": 0, "skipped": 0, "errors": 0}
    if max_runs is None and days is None:
        return summary
    current_time = now or datetime.now(timezone.utc)
    try:
        candidates = []
        for directory in root.iterdir():
            manifest = managed_manifest(directory)
            if manifest is None:
                if directory.name.startswith("run_"):
                    summary["skipped"] += 1
                continue
            created = datetime.fromisoformat(manifest["created_at_utc"].replace("Z", "+00:00"))
            candidates.append((created, directory))
        candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        other_runs = [directory.name for _, directory in candidates if directory.name != keep_run_id]
        keep = {keep_run_id} | set(other_runs[:max(0, max_runs - 1)]) if max_runs is not None else None
        for created, directory in candidates:
            if directory.name == keep_run_id or any(path.resolve().is_relative_to(directory.resolve()) for path in protected_paths):
                continue
            old_by_age = days is not None and (current_time - created).total_seconds() > days * 86400
            old_by_count = keep is not None and directory.name not in keep
            if not (old_by_age or old_by_count):
                continue
            # Recheck immediately before removal, including unexpected user additions.
            if managed_manifest(directory) is None:
                summary["skipped"] += 1
                continue
            try:
                shutil.rmtree(directory)
                summary["removed"] += 1
            except OSError:
                summary["errors"] += 1
    except OSError:
        summary["errors"] += 1
    return summary
