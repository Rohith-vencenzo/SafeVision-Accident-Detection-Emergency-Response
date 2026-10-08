"""Reusable Ultralytics adapter isolated from video decoding and decisions."""

import math
from pathlib import Path
from typing import Any, Protocol

from .checkpoint import CheckpointInfo, load_checkpoint
from .config import InferenceConfig
from .errors import SetupError
from .observations import Detection


class Detector(Protocol):
    info: CheckpointInfo

    def detect(self, frame: Any) -> list[Detection]:
        """Return relevant frame detections in original image coordinates."""
        ...


def resolve_device(requested: str, torch_module: Any) -> str:
    """Fail explicitly when requested acceleration is unavailable."""
    if requested == "cpu":
        return "cpu"
    if requested == "mps":
        if not torch_module.backends.mps.is_available():
            raise SetupError("MPS was requested but is unavailable in this PyTorch environment. Use --device cpu.")
        return "mps"
    index = int(requested.removeprefix("cuda:"))
    if not torch_module.cuda.is_available() or index >= torch_module.cuda.device_count():
        raise SetupError(
            f"CUDA device {index} was requested but is unavailable to installed PyTorch. "
            "Use --device cpu or install and verify compatible CUDA PyTorch wheels in .venv."
        )
    return str(index)


class UltralyticsDetector:
    """Load supplied weights once and reuse the model for all sampled frames."""

    def __init__(self, weights: Path, accident_classes: tuple[str, ...], *, logs: Path,
                 options: InferenceConfig) -> None:
        options.validate()
        self.options = options
        self.model, self.info = load_checkpoint(weights, accident_classes, logs=logs)
        import torch
        self.device = resolve_device(options.device, torch)
        torch.set_num_threads(options.cpu_threads)

    def detect(self, frame: Any) -> list[Detection]:
        """Predict only verified accident-related classes; no tracking/physics claims."""
        try:
            results = self.model.predict(
                source=frame, conf=self.options.confidence, imgsz=self.options.image_size,
                device=self.device, classes=list(self.info.accident_class_ids), verbose=False,
                save=False, show=False, stream=False,
            )
            detections = []
            boxes = results[0].boxes
            if boxes is not None:
                for row in boxes.data.detach().cpu().tolist():
                    x1, y1, x2, y2, confidence, class_id = row[:6]
                    key = int(class_id)
                    if key in self.info.accident_class_ids and confidence >= self.options.confidence:
                        if not all(math.isfinite(value) for value in (x1, y1, x2, y2, confidence)):
                            raise ValueError("model returned nonfinite box coordinates/confidence")
                        detections.append(Detection(key, self.info.class_names[key], float(confidence),
                                                    (float(x1), float(y1), float(x2), float(y2))))
            return detections
        except Exception as exc:
            raise SetupError(
                f"Inference failed: {type(exc).__name__}: {exc}. Check --device and dependencies; "
                "try --device cpu or a smaller --imgsz. No alternate model is loaded."
            ) from exc
