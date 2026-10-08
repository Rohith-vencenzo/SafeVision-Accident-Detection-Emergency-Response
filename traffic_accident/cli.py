"""Practical local-video CLI and setup diagnostics, with clean JSON stdout."""

import argparse
import json
import sys
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .checkpoint import inspect_checkpoint
from .config import ProjectConfig, load_config
from .diagnostics import check_environment
from .errors import SetupError
from .integration import SimulationAlertSink
from .live_alert import LiveAlertEmitter
from .paths import resolve_cli_path
from .video import check_source
from .workflow import analyze_video, human_summary


def build_parser() -> argparse.ArgumentParser:
    """Build the video and setup CLI; source is required outside diagnostic mode."""
    parser = argparse.ArgumentParser(description="Local traffic-accident video prototype — evaluation-ready local workflow (Phase 6).")
    parser.add_argument("--source", type=Path, help="Required for processing: local video path; quote paths containing spaces")
    parser.add_argument("--config", type=Path, help="Config JSON path (default: project config.json)")
    parser.add_argument("--model", type=Path, help="Explicit local .pt checkpoint override")
    display = parser.add_mutually_exclusive_group()
    display.add_argument("--show", action="store_true", default=None, help="Show annotated frames; Q/Escape stops early")
    display.add_argument("--no-show", dest="show", action="store_false", help="Override configured preview; run headless")
    saving = parser.add_mutually_exclusive_group()
    saving.add_argument("--save-output", action="store_true", default=None, help="Save annotated video at original size/FPS")
    saving.add_argument("--no-save", action="store_true", help="Disable video, report, and evidence saving; JSON/summary still available")
    parser.add_argument("--output", type=Path, help="Output .mp4 or .avi path; also enables saving; existing files are preserved")
    parser.add_argument("--output-dir", type=Path, help="Base directory for automatic video output and its incidents/ report bundles")
    parser.add_argument("--no-evidence", action="store_true", help="Save reports without evidence images")
    parser.add_argument("--evidence-context-seconds", type=float, help="Desired before/after context around strongest frame (default 1 second)")
    parser.add_argument("--retain-runs", dest="retention_max_runs", type=int, help="Retain N verified report bundles; disabled by default")
    parser.add_argument("--retention-days", type=float, help="Expire old verified report bundles; disabled by default")
    parser.add_argument("--simulate-alerts", action="store_true", help="Write local simulation-only incident events; no network/emergency action")
    parser.add_argument("--simulation-alert-log", type=Path, help="JSONL path for --simulate-alerts (default: logs/simulation-alerts.jsonl)")
    parser.add_argument("--alert-webhook", help="POST an alert the instant the temporal engine confirms, instead of only at run end")
    parser.add_argument("--alert-token", help="Shared secret sent as X-Detector-Token with --alert-webhook")
    parser.add_argument("--json", action="store_true", help="Print one machine-readable result; logs/errors stay on stderr")
    parser.add_argument("--confidence", "--conf", dest="confidence", type=float, help="Detection score threshold (0, 1]")
    parser.add_argument("--imgsz", "--image-size", dest="image_size", type=int, help="Inference image size (multiple of 32)")
    parser.add_argument("--frame-stride", "--stride", dest="frame_stride", type=int, help="Infer every Nth frame; output retains all decoded frames")
    parser.add_argument("--device", help="cpu, mps, CUDA index (0), or cuda:0; no silent device fallback")
    parser.add_argument("--max-frames", type=int, help="Maximum decoded source frames; limited runs are explicitly marked partial")
    parser.add_argument("--cpu-threads", type=int, help="Maximum PyTorch CPU intra-op threads (default config: 4)")
    parser.add_argument("--fallback-fps", type=float, help="Explicit FPS assumption only when source FPS is zero/invalid")
    parser.add_argument("--event-confidence", dest="support_confidence", type=float, help="Temporal supporting-score threshold, distinct from --confidence")
    parser.add_argument("--event-window-seconds", dest="window_seconds", type=float, help="Rolling video-time evidence window")
    parser.add_argument("--event-min-frames", dest="min_support_frames", type=int, help="Minimum supporting inferred frames (at least 2)")
    parser.add_argument("--event-min-span-seconds", dest="min_support_span_seconds", type=float, help="Minimum first-to-last support span inside the window")
    parser.add_argument("--event-min-ratio", dest="min_positive_ratio", type=float, help="Minimum positive/observed inferred-frame ratio in the window")
    parser.add_argument("--event-min-consecutive", dest="min_consecutive_frames", type=int, help="Minimum current consecutive supporting inferred frames (at least 2)")
    parser.add_argument("--event-max-sample-gap-seconds", dest="max_sample_gap_seconds", type=float, help="Larger gaps reset the confirmation window; no continuity extrapolation")
    parser.add_argument("--event-gap-seconds", dest="event_gap_seconds", type=float, help="Close/group candidates using time since last qualifying support")
    parser.add_argument("--print-config", action="store_true", help="Print validated config with CLI overrides; diagnostics only")
    parser.add_argument("--check-env", action="store_true", help="Check venv, dependencies, and native imports; diagnostics only")
    parser.add_argument("--check-model", action="store_true", help="Inspect actual task/classes; diagnostics only")
    parser.add_argument("--check-source", action="store_true", help="Check --source metadata and first decoded frame; diagnostics only")
    return parser


def apply_overrides(config: ProjectConfig, args: argparse.Namespace) -> ProjectConfig:
    """Apply only explicitly supplied CLI values, then validate effective options."""
    overrides = {key: getattr(args, key) for key in
                 ("confidence", "image_size", "frame_stride", "device", "max_frames", "cpu_threads")
                 if getattr(args, key) is not None}
    inference = replace(config.inference, **overrides)
    video_values = {key: getattr(args, key) for key in ("show", "save_output", "fallback_fps")
                    if getattr(args, key) is not None}
    if args.no_save and args.output is not None:
        raise SetupError("--no-save conflicts with --output. Remove --output to run without saved artifacts.")
    if args.output is not None:
        video_values["save_output"] = True
    if args.no_save:
        video_values["save_output"] = False
    video = replace(config.video, **video_values)
    reporting_values = {key: getattr(args, key) for key in ("evidence_context_seconds", "retention_max_runs", "retention_days")
                        if getattr(args, key) is not None}
    if args.no_save:
        reporting_values["enabled"] = False
    if args.no_evidence:
        reporting_values["save_evidence"] = False
    reporting = replace(config.reporting, **reporting_values)
    decision_values = {key: getattr(args, key) for key in
                       ("support_confidence", "window_seconds", "min_support_frames", "min_support_span_seconds",
                        "min_positive_ratio", "min_consecutive_frames", "max_sample_gap_seconds", "event_gap_seconds")
                       if getattr(args, key) is not None}
    decision = replace(config.decision, **decision_values)
    inference.validate()
    video.validate()
    decision.validate()
    reporting.validate()
    weights = resolve_cli_path(args.model, config.root) if args.model else config.weights
    paths = {}
    if args.output_dir is not None:
        output_dir = resolve_cli_path(args.output_dir, config.root)
        for protected in (weights.parent, config.test_videos, config.logs):
            if output_dir.is_relative_to(protected) or protected.is_relative_to(output_dir):
                raise SetupError("--output-dir must be separate from model, test-video, and log directories.")
        paths = {"output_videos": output_dir, "incidents": output_dir / "incidents"}
    return replace(config, inference=inference, video=video, decision=decision, weights=weights,
                   reporting=reporting, **paths)


def main(argv: list[str] | None = None) -> int:
    """Return 0 on success, 2 on a helpful setup/processing error, 130 on Ctrl+C."""
    parser = build_parser()
    args = parser.parse_args(argv)
    diagnostic = args.print_config or args.check_env or args.check_model or args.check_source
    if args.source is None and not diagnostic:
        parser.error("--source is required for video processing. For setup use --check-model or --print-config.")
    if args.check_source and args.source is None:
        parser.error("--check-source requires --source with a local video path.")
    try:
        # Any dependency chatter during import, loading, or inference goes to stderr.
        with redirect_stdout(sys.stderr):
            config = apply_overrides(load_config(args.config), args)
            source = resolve_cli_path(args.source, config.root) if args.source is not None else None
            if diagnostic:
                result = {}
                if args.print_config:
                    result["config"] = config.as_dict()
                if args.check_env:
                    result["environment"] = check_environment(config.logs)
                if args.check_model:
                    result["model"] = inspect_checkpoint(config.weights, config.accident_classes, logs=config.logs).as_dict()
                if args.check_source:
                    result["source"] = check_source(source, fallback_fps=config.video.fallback_fps)
            else:
                output = None
                if config.video.save_output:
                    output = resolve_cli_path(args.output, config.root) if args.output else config.output_videos / (
                        f"{source.stem}_annotated_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}.mp4"
                    )
                result = analyze_video(source, config, output=output,
                                       progress=lambda message: print(message, file=sys.stderr),
                                       callback=(SimulationAlertSink(resolve_cli_path(args.simulation_alert_log, config.root))
                                                 if args.simulate_alerts else None),
                                       live_alert=(LiveAlertEmitter(args.alert_webhook, args.alert_token or "")
                                                   if args.alert_webhook else None))
        if diagnostic or args.json:
            print(json.dumps(result, indent=2, allow_nan=False))
        else:
            print(human_summary(result))
        return 0
    except SetupError as exc:
        print(f"Setup/processing error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Processing interrupted. Unpublished report staging is cleaned; inspect any already finalized video output.", file=sys.stderr)
        return 130
