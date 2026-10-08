"""Whole-video labeled evaluation with explicit calibration/test split handling."""

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from .config import PROJECT_ROOT, ProjectConfig, ReportingConfig, VideoConfig, load_config
from .errors import SetupError
from .paths import resolve_cli_path
from .workflow import analyze_video


@dataclass(frozen=True)
class GroundTruthEvent:
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class ManifestVideo:
    video_id: str
    path: Path
    label: str
    split: str
    camera_id: str | None
    location_id: str | None
    events: tuple[GroundTruthEvent, ...]


def _keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SetupError(f"{label} must contain exactly: {', '.join(sorted(expected))}.")
    return value


def load_manifest(path: Path, *, root: Path = PROJECT_ROOT, require_files: bool = True) -> tuple[list[ManifestVideo], list[str]]:
    """Parse the manifest without reading video frames; enforce whole-video identity."""
    path = path.expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"Cannot read evaluation manifest {path}: {exc}.") from exc
    data = _keys(data, {"schema_version", "videos"}, "Manifest")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise SetupError("Evaluation manifest schema_version must be integer 1.")
    entries = data["videos"]
    if not isinstance(entries, list):
        raise SetupError("Manifest videos must be a list of complete whole-video entries.")
    videos: list[ManifestVideo] = []
    ids: set[str] = set()
    paths: dict[Path, str] = {}
    for index, entry in enumerate(entries):
        item = _keys(entry, {"id", "path", "label", "split", "camera_id", "location_id", "events"}, f"videos[{index}]")
        video_id, raw_path, label, split = item["id"], item["path"], item["label"], item["split"]
        if not isinstance(video_id, str) or not video_id.strip() or video_id in ids:
            raise SetupError(f"videos[{index}].id must be a unique nonempty string.")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise SetupError(f"videos[{index}].path must be a local path.")
        if label not in ("accident", "normal") or split not in ("calibration", "test"):
            raise SetupError(f"videos[{index}] label must be accident/normal and split calibration/test.")
        video_path = resolve_cli_path(Path(raw_path), root)
        if require_files and not video_path.is_file():
            raise SetupError(f"Manifest video does not exist: {video_path}.")
        if video_path in paths:
            raise SetupError(f"Video path is repeated by {video_id} and {paths[video_path]}; split whole videos, not frames.")
        camera_id, location_id = item["camera_id"], item["location_id"]
        for field, value in (("camera_id", camera_id), ("location_id", location_id)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise SetupError(f"videos[{index}].{field} must be null or a nonempty string.")
        raw_events = item["events"]
        if not isinstance(raw_events, list) or (label == "normal" and raw_events):
            raise SetupError(f"videos[{index}].events must be empty for normal videos and a list for accident videos.")
        events: list[GroundTruthEvent] = []
        for event_index, raw_event in enumerate(raw_events):
            event = _keys(raw_event, {"start_seconds", "end_seconds"}, f"videos[{index}].events[{event_index}]")
            start, end = event["start_seconds"], event["end_seconds"]
            if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float))
                    or not isinstance(end, (int, float)) or start < 0 or end < start):
                raise SetupError(f"videos[{index}].events[{event_index}] requires 0 <= start_seconds <= end_seconds.")
            events.append(GroundTruthEvent(float(start), float(end)))
        if label == "accident" and not events:
            raise SetupError(f"Accident video {video_id} requires at least one labeled event interval.")
        ids.add(video_id)
        paths[video_path] = video_id
        videos.append(ManifestVideo(video_id, video_path, label, split, camera_id, location_id, tuple(events)))
    warnings: list[str] = []
    groups: dict[tuple[str | None, str | None], set[str]] = {}
    for video in videos:
        if video.camera_id is not None or video.location_id is not None:
            groups.setdefault((video.camera_id, video.location_id), set()).add(video.split)
    leaked_groups = [group for group, splits in groups.items() if len(splits) > 1]
    if leaked_groups:
        warnings.append("Some camera/location groups appear in both calibration and test; interpret test metrics as potentially correlated.")
    return videos, warnings


def _interval_overlap(predicted: dict[str, Any], truth: GroundTruthEvent, tolerance: float) -> bool:
    return predicted["start_seconds"] <= truth.end_seconds + tolerance and predicted["end_seconds"] + tolerance >= truth.start_seconds


def _match_incidents(predicted: list[dict[str, Any]], truth: tuple[GroundTruthEvent, ...], tolerance: float) -> tuple[list[tuple[dict[str, Any], GroundTruthEvent]], list[dict[str, Any]], list[GroundTruthEvent]]:
    """Greedy one-to-one interval matching, prioritizing largest overlap then earliest times."""
    pairs: list[tuple[float, int, int, dict[str, Any], GroundTruthEvent]] = []
    for pred_index, prediction in enumerate(predicted):
        for truth_index, target in enumerate(truth):
            if _interval_overlap(prediction, target, tolerance):
                overlap = max(0.0, min(prediction["end_seconds"], target.end_seconds) - max(prediction["start_seconds"], target.start_seconds))
                pairs.append((-overlap, pred_index, truth_index, prediction, target))
    used_predictions: set[int] = set()
    used_truth: set[int] = set()
    matches = []
    for _, pred_index, truth_index, prediction, target in sorted(pairs):
        if pred_index not in used_predictions and truth_index not in used_truth:
            used_predictions.add(pred_index)
            used_truth.add(truth_index)
            matches.append((prediction, target))
    unmatched_predictions = [prediction for index, prediction in enumerate(predicted) if index not in used_predictions]
    unmatched_truth = [target for index, target in enumerate(truth) if index not in used_truth]
    return matches, unmatched_predictions, unmatched_truth


def _safe_divide(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def score_results(entries: Iterable[tuple[ManifestVideo, dict[str, Any]]], *, tolerance_seconds: float = 0.5) -> dict[str, Any]:
    """Compute metrics without converting incomplete runs into negative labels."""
    rows: list[dict[str, Any]] = []
    video_tp = video_fp = video_fn = video_tn = inconclusive = 0
    incident_tp = incident_fp = incident_fn = 0
    delays: list[float] = []
    normal_hours = 0.0
    false_confirmed_incidents = 0
    for video, result in entries:
        if not result.get("complete_video_processed"):
            inconclusive += 1
            rows.append({"id": video.video_id, "label": video.label, "status": "inconclusive_partial", "predicted": None})
            continue
        predicted = result.get("accident_detected") is True
        if video.label == "accident" and predicted:
            video_tp += 1
        elif video.label == "accident":
            video_fn += 1
        elif predicted:
            video_fp += 1
        else:
            video_tn += 1
        predicted_incidents = list(result.get("incidents", []))
        matches, unmatched_predictions, unmatched_truth = _match_incidents(predicted_incidents, video.events, tolerance_seconds)
        incident_tp += len(matches)
        incident_fp += len(unmatched_predictions)
        incident_fn += len(unmatched_truth)
        if video.label == "normal":
            false_confirmed_incidents += len(predicted_incidents)
            duration = result.get("source", {}).get("estimated_duration_seconds") or result.get("last_video_timestamp_seconds") or 0
            normal_hours += duration / 3600
        for prediction, target in matches:
            delays.append(prediction["confirmed_at_seconds"] - target.start_seconds)
        rows.append({
            "id": video.video_id, "path": str(video.path), "label": video.label, "split": video.split,
            "camera_id": video.camera_id, "location_id": video.location_id, "status": "scored",
            "predicted_accident": predicted, "ground_truth_events": [{"start_seconds": event.start_seconds, "end_seconds": event.end_seconds} for event in video.events],
            "predicted_incidents": [{"start_seconds": event["start_seconds"], "end_seconds": event["end_seconds"], "confirmed_at_seconds": event["confirmed_at_seconds"]} for event in predicted_incidents],
            "incident_matches": len(matches), "missed_incidents": len(unmatched_truth),
            "false_confirmed_incidents": len(unmatched_predictions),
            "detection_delays_seconds": [prediction["confirmed_at_seconds"] - target.start_seconds for prediction, target in matches],
        })
    video_precision = _safe_divide(video_tp, video_tp + video_fp)
    video_recall = _safe_divide(video_tp, video_tp + video_fn)
    incident_precision = _safe_divide(incident_tp, incident_tp + incident_fp)
    incident_recall = _safe_divide(incident_tp, incident_tp + incident_fn)
    return {
        "metric_version": "1.0", "tolerance_seconds": tolerance_seconds,
        "video_level": {"true_positive": video_tp, "false_positive": video_fp, "false_negative": video_fn, "true_negative": video_tn,
                         "inconclusive_partial": inconclusive, "precision": video_precision, "recall": video_recall,
                         "f1": _safe_divide(2 * video_precision * video_recall, video_precision + video_recall) if video_precision is not None and video_recall is not None and video_precision + video_recall else None},
        "incident_level": {"true_positive": incident_tp, "false_positive": incident_fp, "false_negative": incident_fn,
                           "precision": incident_precision, "recall": incident_recall,
                           "f1": _safe_divide(2 * incident_precision * incident_recall, incident_precision + incident_recall) if incident_precision is not None and incident_recall is not None and incident_precision + incident_recall else None,
                           "missed_incidents": incident_fn, "mean_detection_delay_seconds": statistics.fmean(delays) if delays else None,
                           "median_detection_delay_seconds": statistics.median(delays) if delays else None},
        "false_confirmed_incidents_per_normal_hour": _safe_divide(false_confirmed_incidents, normal_hours),
        "normal_video_hours": normal_hours, "per_video": rows,
        "limitations": ["Metrics are valid only for the supplied labeled whole videos and split; they do not generalize automatically.", "Inconclusive partial runs are reported separately rather than counted as true negatives.", "Frame-neighbor leakage is prevented by manifest path identity, but camera/location leakage is reported as a warning when metadata overlaps.", "Calibration and final test thresholds must be kept separate; this scorer does not tune thresholds."],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate complete labeled traffic videos by whole-video/incident metrics.")
    parser.add_argument("--manifest", type=Path, required=True, help="Version 1 whole-video label manifest")
    parser.add_argument("--split", choices=("calibration", "test", "all"), default="test")
    parser.add_argument("--config", type=Path, help="Project config path")
    parser.add_argument("--model", type=Path, help="Local checkpoint override")
    parser.add_argument("--confidence", type=float)
    parser.add_argument("--imgsz", type=int, dest="image_size")
    parser.add_argument("--frame-stride", type=int)
    parser.add_argument("--device")
    parser.add_argument("--cpu-threads", type=int)
    parser.add_argument("--max-frames", type=int, help="For smoke testing only; partial runs are inconclusive and not scored")
    parser.add_argument("--tolerance-seconds", type=float, default=0.5)
    parser.add_argument("--max-videos", type=int)
    parser.add_argument("--validate-only", action="store_true", help="Validate manifest/splits without loading weights or videos")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        videos, manifest_warnings = load_manifest(args.manifest, require_files=not args.validate_only)
        selected = videos if args.split == "all" else [video for video in videos if video.split == args.split]
        if args.max_videos is not None:
            if type(args.max_videos) is not int or args.max_videos < 1:
                raise SetupError("--max-videos must be a positive integer.")
            selected = selected[:args.max_videos]
        if args.validate_only:
            result = {"manifest_valid": True, "videos": len(videos), "selected": len(selected), "split": args.split, "warnings": manifest_warnings,
                      "whole_video_split_policy": "each manifest path appears once; labels/events belong to complete clips"}
        else:
            config = load_config(args.config)
            values = {key: getattr(args, key) for key in ("confidence", "image_size", "frame_stride", "device", "cpu_threads", "max_frames") if getattr(args, key) is not None}
            inference = replace(config.inference, **values)
            config = replace(config, inference=inference, weights=resolve_cli_path(args.model, config.root) if args.model else config.weights,
                             video=replace(config.video, show=False, save_output=False), reporting=ReportingConfig(enabled=False))
            config.inference.validate()
            if not selected:
                raise SetupError(f"No videos selected for split {args.split!r}.")
            entries = []
            started = perf_counter()
            for index, video in enumerate(selected, 1):
                print(f"Evaluating {index}/{len(selected)}: {video.video_id}", file=sys.stderr)
                entries.append((video, analyze_video(video.path, config, progress=lambda message: print(message, file=sys.stderr))))
            result = {"manifest": str(Path(args.manifest).resolve()), "split": args.split, "videos_evaluated": len(entries),
                      "elapsed_seconds": perf_counter() - started, "manifest_warnings": manifest_warnings,
                      "metrics": score_results(entries, tolerance_seconds=args.tolerance_seconds),
                      "evaluation_policy": {"whole_video_split": True, "calibration_split": "separate from final test; thresholds are not tuned here", "partial_runs": "inconclusive and excluded from confusion counts"}}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except SetupError as exc:
        print(f"Evaluation error: {exc}", file=sys.stderr)
        return 2
