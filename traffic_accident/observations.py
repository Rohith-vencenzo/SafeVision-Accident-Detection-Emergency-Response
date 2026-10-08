"""Detector-independent frame observations; these are not incident decisions."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    box_xyxy: tuple[float, float, float, float]

    def as_dict(self) -> dict[str, Any]:
        """Return an uncalibrated model score and source-pixel box coordinates."""
        return asdict(self)
