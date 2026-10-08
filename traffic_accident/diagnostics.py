"""Minimal environment import checks; this is not a processing benchmark."""

import platform
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from .errors import SetupError
from .runtime import import_ultralytics


def check_environment(logs: Path) -> dict[str, Any]:
    """Verify the active interpreter and CV/model dependencies can initialize."""
    if sys.prefix == sys.base_prefix:
        raise SetupError("A virtual environment is required. Activate the project's .venv first.")
    import_ultralytics(logs)
    try:
        import cv2
        import torch
        import torchvision
        # Exercise the compiled torchvision op, catching incompatible torch/vision wheels.
        count = torchvision.ops.nms(torch.empty((0, 4)), torch.empty((0,)), 0.5).numel()
        assert count == 0
    except (ImportError, OSError, RuntimeError) as exc:
        raise SetupError(f"CV/PyTorch import or native operation failed: {exc}. Reinstall compatible wheels in .venv.") from exc
    return {
        "python": platform.python_version(), "executable": sys.executable,
        "virtual_environment": sys.prefix, "platform": platform.platform(),
        "dependencies": {name: version(name) for name in
                         ("ultralytics", "torch", "torchvision", "opencv-python", "numpy")},
        "opencv_runtime_version": cv2.__version__,
        "cuda_available_to_pytorch": torch.cuda.is_available(),
        "torch_cuda_build": torch.version.cuda,
    }
