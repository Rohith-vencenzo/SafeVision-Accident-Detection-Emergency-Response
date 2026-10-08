"""Local processing-speed benchmark with explicit resource/settings metadata."""

import argparse
import json
import platform
import sys
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

from .config import ProjectConfig, ReportingConfig, VideoConfig, load_config
from .errors import SetupError
from .paths import resolve_cli_path
from .workflow import analyze_video


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark local traffic-video processing speed; no accuracy claim.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--device")
    parser.add_argument("--imgsz", type=int, dest="image_size")
    parser.add_argument("--frame-stride", type=int)
    parser.add_argument("--cpu-threads", type=int)
    parser.add_argument("--max-frames", type=int, default=300)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser


def run_benchmark(source: Path, config: ProjectConfig, repeats: int) -> dict[str, Any]:
    """Run complete local inference passes without reports/video/evidence."""
    if type(repeats) is not int or repeats < 1:
        raise SetupError("--repeats must be a positive integer.")
    runs = []
    for index in range(repeats):
        started = perf_counter()
        result = analyze_video(source, config, progress=lambda message: print(message, file=sys.stderr))
        elapsed = perf_counter() - started
        processed_duration = result.get("last_video_timestamp_seconds") or 0
        source_duration = result["source"].get("estimated_duration_seconds") or processed_duration
        runs.append({"repeat": index + 1, "elapsed_seconds": elapsed, "decoded_frames": result["decoded_frames"],
                     "inferred_frames": result["inferred_frames"], "processed_duration_seconds": processed_duration,
                     "source_duration_seconds": source_duration, "complete_video_processed": result["complete_video_processed"],
                     "decoded_fps_wall": result["decoded_frames"] / elapsed if elapsed else None,
                     "inferred_fps_wall": result["inferred_frames"] / elapsed if elapsed else None,
                     "inference_fps_excluding_decode": result["inferred_frames"] / result["timings"]["inference_seconds"] if result["timings"]["inference_seconds"] else None,
                     "real_time_factor": elapsed / processed_duration if processed_duration else None,
                     "result_timing_seconds": result["timings"]})
    return {"benchmark_version": "1.0", "machine": {"python": platform.python_version(), "platform": platform.platform(), "executable": sys.executable},
            "source": str(source), "settings": {"confidence": config.inference.confidence, "image_size": config.inference.image_size,
                                                   "frame_stride": config.inference.frame_stride, "device": config.inference.device,
                                                   "cpu_threads": config.inference.cpu_threads, "max_frames": config.inference.max_frames},
            "runs": runs, "limitations": ["Local benchmark only; storage/filesystem, model warm-up, and CPU load affect results.", "Speed is not accuracy and does not establish real-time suitability.", "Thresholds were not changed to improve benchmark appearance."]}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        values = {key: getattr(args, key) for key in ("image_size", "frame_stride", "device", "cpu_threads") if getattr(args, key) is not None}
        config = replace(config, inference=replace(config.inference, **values, max_frames=args.max_frames),
                         video=replace(config.video, show=False, save_output=False), reporting=ReportingConfig(enabled=False),
                         weights=resolve_cli_path(args.model, config.root) if args.model else config.weights)
        config.inference.validate()
        source = resolve_cli_path(args.source, config.root)
        result = run_benchmark(source, config, args.repeats)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except SetupError as exc:
        print(f"Benchmark error: {exc}", file=sys.stderr)
        return 2
