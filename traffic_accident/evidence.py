"""Bounded evidence selection and sequential re-decoding without repeat inference."""

import hashlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .atomic import atomic_write
from .config import ProjectConfig
from .errors import SetupError
from .video import VideoReader, get_cv2


@dataclass(frozen=True, slots=True)
class FrameStamp:
    index: int
    timestamp_seconds: float
    timestamp_source: str


@dataclass(frozen=True)
class EvidenceSelection:
    incident_id: str
    role: str
    stamp: FrameStamp
    target_seconds: float
    offset_seconds: float
    context_limited: bool


def select_evidence(incidents: Sequence[dict[str, Any]], timeline: Sequence[FrameStamp],
                    context_seconds: float) -> tuple[list[EvidenceSelection], list[str]]:
    """Select distinct before/strongest/after processed frames, preserving boundary limits."""
    selections: list[EvidenceSelection] = []
    warnings: list[str] = []
    times = [stamp.timestamp_seconds for stamp in timeline]
    indices = {stamp.index: slot for slot, stamp in enumerate(timeline)}
    for incident in incidents:
        anchor = incident["strongest_frame"]
        slot = indices.get(anchor["frame_index"])
        if slot is None:
            warnings.append(f"Evidence unavailable for {incident['incident_id']}: strongest frame was not in the processed timeline.")
            continue
        anchor_time = timeline[slot].timestamp_seconds
        for role, offset in (("before", -context_seconds), ("strongest", 0.0), ("after", context_seconds)):
            target = anchor_time + offset
            if role == "strongest":
                chosen = slot
            elif role == "before" and slot > 0:
                chosen = min(slot - 1, max(0, bisect_right(times, target) - 1))
            elif role == "after" and slot < len(timeline) - 1:
                chosen = max(slot + 1, min(len(timeline) - 1, bisect_left(times, target)))
            else:
                warnings.append(f"No {role} context is available inside the processed clip for {incident['incident_id']}.")
                continue
            stamp = timeline[chosen]
            actual_offset = stamp.timestamp_seconds - anchor_time
            limited = role != "strongest" and abs(actual_offset) + 1e-9 < context_seconds
            if limited:
                warnings.append(f"{role.capitalize()} evidence context is shorter than requested at the processing boundary for {incident['incident_id']}.")
            selections.append(EvidenceSelection(incident["incident_id"], role, stamp, target, actual_offset, limited))
    return selections, warnings


def save_evidence(source: Path, stage: Path, incidents: list[dict[str, Any]],
                  timeline: Sequence[FrameStamp], config: ProjectConfig) -> list[str]:
    """Keep one decoded image at a time and write at most three JPEGs per incident."""
    selections, warnings = select_evidence(incidents, timeline, config.reporting.evidence_context_seconds)
    targets: dict[int, list[EvidenceSelection]] = {}
    by_id = {incident["incident_id"]: incident for incident in incidents}
    for selection in selections:
        targets.setdefault(selection.stamp.index, []).append(selection)
    if not targets:
        for incident in incidents:
            incident["evidence_status"] = "unavailable"
        return warnings
    cv2 = get_cv2()
    try:
        reader = VideoReader(source, fallback_fps=config.video.fallback_fps)
    except SetupError:
        warnings.append("Evidence decoding could not initialize; incident metadata is preserved without images.")
        reader = None
    if reader is not None:
        with reader:
            while targets:
                try:
                    packet = reader.read_next()
                except SetupError:
                    warnings.append("Evidence decoding stopped unexpectedly; missing evidence images are explicitly recorded.")
                    break
                if packet is None:
                    break
                if packet.index not in targets:
                    continue
                plans = targets.pop(packet.index)
                if any(abs(packet.timestamp_seconds - plan.stamp.timestamp_seconds) > 1e-6 for plan in plans):
                    warnings.append("Evidence decoder timing changed; affected frames were omitted instead of mislabeling them.")
                    continue
                ok, encoded = cv2.imencode(".jpg", packet.image, [cv2.IMWRITE_JPEG_QUALITY, config.reporting.jpeg_quality])
                if not ok:
                    raise SetupError("OpenCV could not encode a selected evidence image. No partial incident bundle was published.")
                payload = encoded.tobytes()
                digest = hashlib.sha256(payload).hexdigest()
                for plan in plans:
                    relative = f"evidence/{plan.incident_id}/{plan.role}.jpg"
                    atomic_write(stage / relative, payload)
                    by_id[plan.incident_id]["evidence_frames"].append({
                        "role": plan.role, "path": relative, "image_kind": "source_frame",
                        "frame_index": packet.index, "timestamp_seconds": packet.timestamp_seconds,
                        "timestamp_source": packet.timestamp_source, "sha256": digest,
                        "selection_target_seconds": plan.target_seconds,
                        "offset_from_strongest_seconds": plan.offset_seconds, "context_limited": plan.context_limited,
                    })
    for incident in incidents:
        saved_roles = {item["role"] for item in incident["evidence_frames"]}
        planned_roles = {item.role for item in selections if item.incident_id == incident["incident_id"]}
        incident["evidence_frames"].sort(key=lambda item: ("before", "strongest", "after").index(item["role"]))
        incident["evidence_status"] = "saved" if saved_roles == planned_roles and "strongest" in saved_roles else (
            "partial" if saved_roles else "unavailable"
        )
        if saved_roles != planned_roles:
            warnings.append(f"Some selected evidence frames could not be decoded for {incident['incident_id']}; absent paths were not fabricated.")
    return warnings
