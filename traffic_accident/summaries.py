"""Readable terminal summaries shared with the saved human-readable report."""

from typing import Any


def human_summary(result: dict[str, Any]) -> str:
    """Explain confirmation, coverage, evidence paths, and uncertainty for every clip."""
    lines = [
        f"Temporal incident decision: {result['incident_decision'].upper().replace('_', ' ')} (prototype rules; human review needed)",
        f"Confirmed candidates: {len(result['incidents'])}; rejected: {len(result['rejected_candidates'])}; unresolved: {len(result['unresolved_candidates'])}.",
        f"Decoded {result['decoded_frames']} frames; inferred {result['inferred_frames']}.",
        f"Frames with accident-class detections: {result['frames_with_accident_detections']}.",
        f"Complete clip processed: {'yes' if result['complete_video_processed'] else 'no'} ({result['termination_reason']}).",
    ]
    strongest = result["strongest_frame_observation"]
    if strongest is not None:
        lines.append(f"Strongest frame observation: {strongest['timestamp_seconds']:.3f}s, score {strongest['confidence']:.3f} (uncalibrated; separate from temporal decision).")
    else:
        lines.append("No accident-class boxes met the configured threshold on sampled frames; this does not rule out an accident.")
    for candidate in result["incidents"]:
        identity = candidate.get("incident_id") or str(candidate["candidate_number"])
        lines.append(f"Incident {identity}: support interval {candidate['start_seconds']:.3f}–{candidate['end_seconds']:.3f}s; confirmed at {candidate['confirmed_at_seconds']:.3f}s; {candidate['supporting_frame_count']} supporting frames.")
        if "evidence_status" in candidate:
            lines.append(f"  Evidence: {candidate['evidence_status']} ({len(candidate['evidence_frames'])} images).")
    if result["incident_decision"] == "no_confirmed_incident":
        lines.append("No candidate passed the temporal rules; this does not prove the video is accident-free.")
    if result["incident_decision"] == "inconclusive":
        lines.append("Partial video coverage prevents a whole-video negative decision.")
    if result["annotated_video"]:
        lines.append(f"Annotated video: {result['annotated_video']}")
    artifacts = result.get("artifact_paths", {})
    if artifacts.get("json_report"):
        lines.append(f"JSON report: {artifacts['json_report']}")
        lines.append(f"Human report: {artifacts['human_report']}")
    lines.append(f"Processing elapsed: {result['timings']['elapsed_seconds']:.3f}s (see timing scope in JSON).")
    lines.extend(f"Warning: {message}" for message in result["warnings"])
    return "\n".join(lines)
