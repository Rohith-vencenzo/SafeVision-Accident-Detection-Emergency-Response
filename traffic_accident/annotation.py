"""Readable live overlays separating raw frame boxes from temporal incident state."""

from typing import Any, Mapping

from .config import OverlayConfig
from .observations import Detection
from .video import VideoFrame, get_cv2

COLORS = {"NORMAL": (210, 210, 210), "POSSIBLE": (0, 220, 255),
          "VERIFYING": (0, 170, 255), "CONFIRMED": (70, 90, 255), "FALSE_ALARM": (180, 180, 180)}


def overlay_lines(packet: VideoFrame, *, sampled: bool, detection_count: int,
                  decision: Mapping[str, Any]) -> list[str]:
    """Return truthful display text, independently testable without GUI/pixel OCR."""
    time = packet.timestamp_seconds
    time_label = f"{int(time // 60):02d}:{time % 60:06.3f}"
    if packet.timestamp_source == "fps_fallback":
        time_label += " (estimated)"
    state = decision["state"]
    candidate = decision["candidate_number"]
    state_label = f"State: {state}" + (f" | Candidate {candidate}" if candidate is not None else " (idle)")
    if state == "CONFIRMED":
        status = "RULE-CONFIRMED - human review required"
    elif state in ("POSSIBLE", "VERIFYING"):
        status = "Verification pending - incident NOT confirmed"
    else:
        status = "No active verification - not a safety assessment"
    sampling = f"Fresh model boxes: {detection_count}" if sampled else "Inference skipped - no fresh boxes"
    return [f"Video {time_label} | {sampling}", state_label, status,
            f"Confirmed: {decision['confirmed_count']} | Rejected: {decision['rejected_count']} | Scores uncalibrated"]


def _fit_text(cv2: Any, text: str, scale: float, width: int) -> tuple[str, float]:
    """Keep text inside the frame; small fixtures get compact/truncated labels."""
    text_width = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0]
    if text_width > width:
        scale = max(0.3, scale * width / max(text_width, 1))
    while text and cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0] > width:
        text = text[:-1]
    return text, scale


def annotate_frame(packet: VideoFrame, detections: list[Detection], *, sampled: bool,
                   decision: Mapping[str, Any], options: OverlayConfig) -> Any:
    """Render fresh boxes and current state; never label pending evidence confirmed."""
    image = packet.image.copy()
    if not options.enabled:
        return image
    cv2 = get_cv2()
    height, width = image.shape[:2]
    scale = min(options.font_scale, max(0.3, width / 1000))
    if options.show_boxes:
        for detection in detections:
            x1, y1, x2, y2 = (round(value) for value in detection.box_xyxy)
            x1, x2 = max(0, min(width - 1, x1)), max(0, min(width - 1, x2))
            y1, y2 = max(0, min(height - 1, y1)), max(0, min(height - 1, y2))
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 180, 255), options.line_thickness)
            label, label_scale = _fit_text(cv2, f"{detection.class_name} {detection.confidence:.2f} [model score]", scale, width - x1 - 2)
            cv2.putText(image, label, (x1, max(12, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                        label_scale, (0, 180, 255), 1, cv2.LINE_AA)
    lines = overlay_lines(packet, sampled=sampled, detection_count=len(detections), decision=decision)
    line_height = max(12, round(24 * scale / 0.55))
    banner_height = min(height, line_height * len(lines) + 8)
    top = 0 if options.position == "top" else height - banner_height
    banner = image[top:top + banner_height].copy()
    banner[:] = (20, 20, 20)
    image[top:top + banner_height] = cv2.addWeighted(banner, options.banner_opacity,
                                                  image[top:top + banner_height], 1 - options.banner_opacity, 0)
    for index, line in enumerate(lines):
        baseline = top + 4 + (index + 1) * line_height - 3
        if baseline >= height:
            break
        text, text_scale = _fit_text(cv2, line, scale, max(1, width - 12))
        color = COLORS.get(decision["state"], (255, 255, 255)) if index in (1, 2) else (255, 255, 255)
        cv2.putText(image, text, (6, baseline), cv2.FONT_HERSHEY_SIMPLEX, text_scale, color, 1, cv2.LINE_AA)
    return image
