"""Keep dependency-generated settings and caches inside the project."""

import os
from pathlib import Path
from typing import Any

from .errors import SetupError


def prepare_runtime(logs: Path) -> None:
    """Configure offline Ultralytics use before importing the dependency."""
    try:
        (logs / "ultralytics" / "Ultralytics").mkdir(parents=True, exist_ok=True)
        (logs / "matplotlib").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SetupError(f"Cannot create local runtime directories under {logs}: {exc}.") from exc
    os.environ["YOLO_CONFIG_DIR"] = str(logs / "ultralytics")
    os.environ["MPLCONFIGDIR"] = str(logs / "matplotlib")
    os.environ["YOLO_OFFLINE"] = "true"


def import_ultralytics(logs: Path) -> type[Any]:
    """Import offline Ultralytics with analytics disabled; return its YOLO class."""
    prepare_runtime(logs)
    try:
        from ultralytics import YOLO, settings
        settings.update({"sync": False, "runs_dir": str(logs / "ultralytics" / "runs")})
    except (ImportError, OSError, RuntimeError) as exc:
        raise SetupError(
            f"Ultralytics could not initialize: {exc}. Activate .venv and run python -m pip install -r requirements.txt."
        ) from exc
    return YOLO
