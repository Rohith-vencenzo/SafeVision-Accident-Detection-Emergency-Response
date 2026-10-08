"""Inspect an explicitly supplied local checkpoint without substituting weights."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import SetupError
from .runtime import import_ultralytics


@dataclass(frozen=True)
class CheckpointInfo:
    path: Path
    task: str
    class_names: dict[int, str]
    accident_class_ids: tuple[int, ...]
    size_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        """Return model metadata, explicitly excluding any accuracy claim."""
        return {
            "path": str(self.path), "task": self.task, "class_names": self.class_names,
            "accident_class_ids": list(self.accident_class_ids),
            "size_bytes": self.size_bytes, "sha256": self.sha256,
        }


def validate_class_mapping(task: str, names: Mapping[int, str], expected: tuple[str, ...]) -> tuple[int, ...]:
    """Resolve explicitly configured names against actual checkpoint metadata."""
    if task != "detect":
        raise SetupError(f"Checkpoint task is {task!r}; this project requires a detection checkpoint (detect).")
    if not names or any(type(key) is not int or key < 0 or not isinstance(value, str)
                        or not value.strip() for key, value in names.items()):
        raise SetupError("Checkpoint has invalid or missing detection class names.")
    missing = set(expected) - set(names.values())
    if not expected or missing:
        raise SetupError(
            f"Checkpoint classes are {dict(names)}; configured accident classes {expected} do not match. "
            "Supply/confirm an accident-specific model and its actual labels; no fallback model is used."
        )
    return tuple(sorted(key for key, value in names.items() if value in expected))


def load_checkpoint(path: Path, accident_classes: tuple[str, ...], *, logs: Path) -> tuple[Any, CheckpointInfo]:
    """Load once, returning the reusable model and verified public metadata."""
    path = path.expanduser().resolve()
    if path.suffix.lower() != ".pt" or not path.is_file():
        raise SetupError(
            f"Model checkpoint is missing or not a local .pt file: {path}. "
            "Place your approved accident-specific weights at models/yolov11.pt. No weights are downloaded."
        )
    try:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        size = path.stat().st_size
    except OSError as exc:
        raise SetupError(f"Model file is not readable: {path}: {exc}.") from exc
    if size == 0:
        raise SetupError(f"Model file is empty: {path}. Supply an intact checkpoint.")
    yolo = import_ultralytics(logs)
    try:
        model = yolo(str(path), verbose=False)
        task = model.task
        raw_names = model.names
        names = dict(enumerate(raw_names)) if isinstance(raw_names, list) else dict(raw_names)
    except Exception as exc:
        raise SetupError(
            f"Cannot load checkpoint {path}: {type(exc).__name__}: {exc}. "
            "Check that the file is intact and compatible with installed Ultralytics/PyTorch."
        ) from exc
    ids = validate_class_mapping(task, names, accident_classes)
    return model, CheckpointInfo(path, task, names, ids, size, digest)


def inspect_checkpoint(path: Path, accident_classes: tuple[str, ...], *, logs: Path) -> CheckpointInfo:
    """Run diagnostic inspection using the same loading path as inference."""
    return load_checkpoint(path, accident_classes, logs=logs)[1]
