"""Validated local configuration; relative paths are project-root-relative."""

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import SetupError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class InferenceConfig:
    confidence: float = 0.4
    image_size: int = 640
    frame_stride: int = 1
    device: str = "cpu"
    max_frames: int | None = None
    cpu_threads: int = 4

    def validate(self) -> None:
        """Reject invalid resource controls before opening videos or models."""
        if (isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))
                or not math.isfinite(self.confidence) or not 0 < self.confidence <= 1):
            raise SetupError("confidence must be finite and greater than 0, up to 1.")
        if type(self.image_size) is not int or not 32 <= self.image_size <= 4096 or self.image_size % 32:
            raise SetupError("image_size/--imgsz must be a multiple of 32 between 32 and 4096.")
        for key in ("frame_stride", "cpu_threads"):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise SetupError(f"{key} must be a positive integer.")
        if self.max_frames is not None and (type(self.max_frames) is not int or self.max_frames < 1):
            raise SetupError("max_frames must be null or a positive decoded-frame limit.")
        if not isinstance(self.device, str) or not re.fullmatch(r"cpu|mps|(?:cuda:)?\d+", self.device):
            raise SetupError("device must be cpu, mps, a CUDA index such as 0, or cuda:0.")


@dataclass(frozen=True)
class VideoConfig:
    fallback_fps: float = 30.0
    show: bool = False
    save_output: bool = False

    def validate(self) -> None:
        """Check explicit FPS assumptions and boolean output/display defaults."""
        if (isinstance(self.fallback_fps, bool) or not isinstance(self.fallback_fps, (int, float))
                or not math.isfinite(self.fallback_fps) or not 0 < self.fallback_fps <= 240):
            raise SetupError("fallback_fps must be finite, greater than 0, and at most 240.")
        if type(self.show) is not bool or type(self.save_output) is not bool:
            raise SetupError("video.show and video.save_output must be JSON booleans.")


@dataclass(frozen=True)
class DecisionConfig:
    """Uncalibrated temporal rules, independent of detector confidence filtering."""

    support_confidence: float = 0.6
    window_seconds: float = 1.5
    min_support_frames: int = 3
    min_support_span_seconds: float = 0.5
    min_positive_ratio: float = 0.6
    min_consecutive_frames: int = 3
    max_sample_gap_seconds: float = 0.5
    event_gap_seconds: float = 1.0

    def validate(self) -> None:
        """Require multi-frame evidence even if the user relaxes other thresholds."""
        for key in ("support_confidence", "min_positive_ratio"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1:
                raise SetupError(f"decision.{key} must be finite and in (0, 1].")
        for key in ("window_seconds", "min_support_span_seconds", "max_sample_gap_seconds", "event_gap_seconds"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise SetupError(f"decision.{key} must be a positive finite number of video seconds.")
        for key in ("min_support_frames", "min_consecutive_frames"):
            value = getattr(self, key)
            if type(value) is not int or value < 2:
                raise SetupError(f"decision.{key} must be an integer of at least 2; one frame cannot confirm an incident.")
        if self.min_support_span_seconds > self.window_seconds:
            raise SetupError("decision.min_support_span_seconds cannot exceed window_seconds.")
        if self.max_sample_gap_seconds > self.event_gap_seconds:
            raise SetupError("decision.max_sample_gap_seconds cannot exceed event_gap_seconds.")


@dataclass(frozen=True)
class OverlayConfig:
    enabled: bool = True
    show_boxes: bool = True
    font_scale: float = 0.55
    line_thickness: int = 2
    banner_opacity: float = 0.8
    position: str = "bottom"

    def validate(self) -> None:
        if type(self.enabled) is not bool or type(self.show_boxes) is not bool:
            raise SetupError("overlay.enabled and show_boxes must be JSON booleans.")
        for key, low, high in (("font_scale", 0.3, 2), ("banner_opacity", 0, 1)):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise SetupError(f"overlay.{key} must be finite and between {low} and {high}.")
        if type(self.line_thickness) is not int or not 1 <= self.line_thickness <= 8:
            raise SetupError("overlay.line_thickness must be an integer between 1 and 8.")
        if self.position not in ("top", "bottom"):
            raise SetupError("overlay.position must be top or bottom.")


@dataclass(frozen=True)
class ReportingConfig:
    enabled: bool = True
    save_evidence: bool = True
    evidence_context_seconds: float = 1.0
    jpeg_quality: int = 90
    retention_max_runs: int | None = None
    retention_days: float | None = None

    def validate(self) -> None:
        if type(self.enabled) is not bool or type(self.save_evidence) is not bool:
            raise SetupError("reporting.enabled and save_evidence must be JSON booleans.")
        value = self.evidence_context_seconds
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 30:
            raise SetupError("reporting.evidence_context_seconds must be finite, greater than 0, and at most 30.")
        if type(self.jpeg_quality) is not int or not 1 <= self.jpeg_quality <= 100:
            raise SetupError("reporting.jpeg_quality must be an integer between 1 and 100.")
        if self.retention_max_runs is not None and (type(self.retention_max_runs) is not int or self.retention_max_runs < 1):
            raise SetupError("reporting.retention_max_runs must be null or a positive integer.")
        value = self.retention_days
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
            raise SetupError("reporting.retention_days must be null or positive finite days.")


@dataclass(frozen=True)
class RegisteredMetadata:
    camera_id: str | None = None
    location_label: str | None = None

    def validate(self) -> None:
        for key in ("camera_id", "location_label"):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200
                                      or any(ord(character) < 32 for character in value)):
                raise SetupError(f"registered_metadata.{key} must be null or a nonempty single-line label up to 200 characters.")


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    weights: Path
    accident_classes: tuple[str, ...]
    test_videos: Path
    output_videos: Path
    incidents: Path
    logs: Path
    inference: InferenceConfig = InferenceConfig()
    video: VideoConfig = VideoConfig()
    decision: DecisionConfig = DecisionConfig()
    overlay: OverlayConfig = OverlayConfig()
    reporting: ReportingConfig = ReportingConfig()
    registered_metadata: RegisteredMetadata = RegisteredMetadata()

    def as_dict(self) -> dict[str, Any]:
        """Return resolved configuration suitable for setup diagnostics."""
        return {
            "schema_version": 1,
            "project_root": str(self.root),
            "model": {"weights": str(self.weights), "accident_classes": list(self.accident_classes)},
            "paths": {key: str(getattr(self, key)) for key in
                      ("test_videos", "output_videos", "incidents", "logs")},
            "inference": asdict(self.inference),
            "video": asdict(self.video),
            "decision": asdict(self.decision),
            "overlay": asdict(self.overlay),
            "reporting": asdict(self.reporting),
            "registered_metadata": asdict(self.registered_metadata),
        }


def _keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SetupError(f"{label} must contain exactly these keys: {', '.join(sorted(expected))}.")
    return value


def _local_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise SetupError(f"{label} must be a nonempty project-relative path.")
    relative = Path(value)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or path == root:
        raise SetupError(f"{label} must stay inside the project root: {root}.")
    return path


def load_config(config_path: Path | None = None, *, root: Path = PROJECT_ROOT) -> ProjectConfig:
    """Load strict JSON config without creating directories or loading a model."""
    root = root.resolve()
    path = config_path or root / "config.json"
    if not path.is_absolute():
        path = root / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"Cannot read configuration {path}: {exc}. Use the supplied config.json.") from exc
    if not isinstance(data, dict):
        raise SetupError("Configuration must be a JSON object.")
    optional_blocks = {"inference", "video", "decision", "overlay", "reporting", "registered_metadata"}
    data = _keys(data, {"schema_version", "model", "paths"} | (optional_blocks & set(data)), "Configuration")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise SetupError("Unsupported config schema_version; expected integer 1.")
    model = _keys(data["model"], {"weights", "accident_classes"}, "model")
    names = model["accident_classes"]
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name.strip() for name in names)):
        raise SetupError("model.accident_classes must be a nonempty list of verified class names.")
    if len(set(names)) != len(names):
        raise SetupError("model.accident_classes must not contain duplicates.")
    paths = _keys(data["paths"], {"test_videos", "output_videos", "incidents", "logs"}, "paths")
    resolved = {key: _local_path(root, value, f"paths.{key}") for key, value in paths.items()}
    weights = _local_path(root, model["weights"], "model.weights")
    if weights.suffix.lower() != ".pt":
        raise SetupError("model.weights must reference a local Ultralytics .pt checkpoint.")
    writable = [resolved[key] for key in ("output_videos", "incidents", "logs")]
    protected = [resolved["test_videos"], weights.parent]
    for index, target in enumerate(writable):
        for other in protected + writable[index + 1:]:
            if target.is_relative_to(other) or other.is_relative_to(target):
                raise SetupError("Output, incident, and log directories must be separate from inputs/models and each other.")
    inference = InferenceConfig(**_keys(data["inference"], set(asdict(InferenceConfig())), "inference")) if "inference" in data else InferenceConfig()
    video = VideoConfig(**_keys(data["video"], set(asdict(VideoConfig())), "video")) if "video" in data else VideoConfig()
    decision = DecisionConfig(**_keys(data["decision"], set(asdict(DecisionConfig())), "decision")) if "decision" in data else DecisionConfig()
    overlay = OverlayConfig(**_keys(data["overlay"], set(asdict(OverlayConfig())), "overlay")) if "overlay" in data else OverlayConfig()
    reporting = ReportingConfig(**_keys(data["reporting"], set(asdict(ReportingConfig())), "reporting")) if "reporting" in data else ReportingConfig()
    metadata = RegisteredMetadata(**_keys(data["registered_metadata"], set(asdict(RegisteredMetadata())), "registered_metadata")) if "registered_metadata" in data else RegisteredMetadata()
    inference.validate()
    video.validate()
    decision.validate()
    overlay.validate()
    reporting.validate()
    metadata.validate()
    return ProjectConfig(root, weights, tuple(names), **resolved, inference=inference, video=video, decision=decision,
                         overlay=overlay, reporting=reporting, registered_metadata=metadata)
