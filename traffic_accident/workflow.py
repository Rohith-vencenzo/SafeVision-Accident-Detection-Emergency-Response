"""Local video pipeline keeping model observations and temporal decisions distinct."""

from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from .annotation import annotate_frame
from .config import ProjectConfig
from .contracts import prepare_result
from .detector import Detector, UltralyticsDetector
from .decision import TemporalDecisionEngine
from .evidence import FrameStamp
from .fingerprint import fingerprint_source
from .integration import CallbackDispatcher, IncidentCallback
from .preview import VideoPreview
from .reporting import publish_result
from .summaries import human_summary
from .video import VideoOutput, VideoReader


def analyze_video(source: Path, config: ProjectConfig, *, output: Path | None = None,
                  progress: Callable[[str], None] | None = None,
                  detector: Detector | None = None,
                  callback: IncidentCallback | None = None) -> dict[str, Any]:
    """Process source frames once, sample inference, and retain positive observations.

    max_frames counts decoded source frames. Saving always writes every decoded
    frame at original dimensions/FPS, regardless of inference stride. The optional
    detector parameter supports small offline integration fixtures.
    """
    started = perf_counter()
    options = config.inference
    options.validate()
    config.video.validate()
    config.decision.validate()
    config.overlay.validate()
    config.reporting.validate()
    config.registered_metadata.validate()
    timeline: list[FrameStamp] = []
    observations: list[dict[str, Any]] = []
    inference_count = 0
    inference_seconds = 0.0
    strongest: dict[str, Any] | None = None
    last_timestamp: float | None = None
    reason = "end_of_video"
    with ExitStack() as resources:
        reader = resources.enter_context(VideoReader(source, fallback_fps=config.video.fallback_fps))
        fingerprint_started = perf_counter()
        fingerprint = fingerprint_source(source)
        fingerprint_seconds = perf_counter() - fingerprint_started
        preview = resources.enter_context(VideoPreview()) if config.video.show else None
        writer = resources.enter_context(VideoOutput(output, source, reader.info)) if output is not None else None
        model_start = perf_counter()
        if detector is None:
            detector = UltralyticsDetector(config.weights, config.accident_classes, logs=config.logs, options=options)
        engine = TemporalDecisionEngine(
            {key: detector.info.class_names[key] for key in detector.info.accident_class_ids}, config.decision
        )
        model_setup_seconds = perf_counter() - model_start
        if progress:
            progress(f"Processing local video at {reader.info.effective_fps:g} source FPS; inference stride {options.frame_stride}, device {options.device}.")
        while True:
            if options.max_frames is not None and reader.decoded_count >= options.max_frames:
                reason = "max_frames"
                break
            packet = reader.read_next()
            if packet is None:
                break
            last_timestamp = packet.timestamp_seconds
            if config.reporting.enabled and config.reporting.save_evidence:
                timeline.append(FrameStamp(packet.index, packet.timestamp_seconds, packet.timestamp_source))
            sampled = packet.index % options.frame_stride == 0
            detections = []
            if sampled:
                inference_start = perf_counter()
                detections = detector.detect(packet.image)
                inference_seconds += perf_counter() - inference_start
                inference_count += 1
                engine.observe(packet.index, packet.timestamp_seconds, detections, timestamp_source=packet.timestamp_source)
                if detections:
                    observation = {"frame_index": packet.index,
                                   "timestamp_seconds": packet.timestamp_seconds,
                                   "timestamp_source": packet.timestamp_source,
                                   "detections": [detection.as_dict() for detection in detections]}
                    observations.append(observation)
                    score = max(item.confidence for item in detections)
                    if strongest is None or score > strongest["confidence"]:
                        strongest = {"frame_index": packet.index, "timestamp_seconds": packet.timestamp_seconds,
                                      "confidence": score, "timestamp_source": packet.timestamp_source}
            else:
                engine.advance(packet.timestamp_seconds)
            if writer is not None or preview is not None:
                annotated = annotate_frame(packet, detections, sampled=sampled,
                                           decision=engine.snapshot(), options=config.overlay)
                if writer is not None:
                    writer.write(annotated)
                if preview is not None and not preview.show(annotated):
                    reason = "stopped_by_user"
                    break
            if progress and reader.decoded_count % 100 == 0:
                progress(f"Decoded {reader.decoded_count} frames; inferred {inference_count}; video time {packet.timestamp_seconds:.3f}s.")
        total = reader.info.metadata_frame_count
        early_eof = reader.reached_eof and total is not None and reader.decoded_count < total
        if early_eof:
            reason = "decode_stopped_early"
        complete = reader.reached_eof and not early_eof
        decision_result = engine.finish(last_timestamp or 0.0, complete=complete, reason=reason)
        fingerprint.verify_unchanged(source)
        warnings = reader.warnings + reader.timestamp_warnings()
        nominal_sample_interval = options.frame_stride / reader.info.effective_fps
        if nominal_sample_interval > config.decision.max_sample_gap_seconds + 1e-9:
            warnings.append("Inference cadence exceeds decision.max_sample_gap_seconds; the temporal engine will not infer continuity across those gaps. Reduce stride or deliberately recalibrate temporal settings.")
        if (max(config.decision.min_support_frames, config.decision.min_consecutive_frames) - 1) * nominal_sample_interval > config.decision.window_seconds + 1e-9:
            warnings.append("Configured inference cadence cannot fit the minimum supporting/consecutive frame counts in the decision window.")
        if options.confidence > config.decision.support_confidence:
            warnings.append("Detector confidence filtering is stricter than temporal support_confidence; the decision engine cannot recover boxes filtered out by the detector.")
        if reason in ("max_frames", "stopped_by_user"):
            warnings.append(f"Processing stopped due to {reason}; the result covers only the decoded portion, not the complete video.")
        if options.frame_stride > 1:
            warnings.append("Inference frame stride skips observations and can miss brief accident-like imagery; all decoded frames are still written if saving.")
        if output is not None:
            warnings.append("Annotated output uses constant source/assumed FPS; variable-frame-rate timing cannot be preserved exactly by OpenCV VideoWriter.")
        result = {
            "phase": 4,
            "analysis_kind": "temporal_incident_decision",
            **decision_result,
            "source": reader.info.as_dict(),
            "model": detector.info.as_dict(),
            "inference_config": asdict(options),
            "decision_config": asdict(config.decision),
            "complete_video_processed": complete,
            "termination_reason": reason,
            "decoded_frames": reader.decoded_count,
            "inferred_frames": inference_count,
            "frames_with_accident_detections": len(observations),
            "last_video_timestamp_seconds": last_timestamp,
            "timestamp_counts": {"decoder": reader.clock.decoder_count, "fps_fallback": reader.clock.fallback_count},
            "strongest_frame_observation": strongest,
            "frame_observations": observations,
            "annotated_video": str(output) if output is not None else None,
            "output_frames": writer.count if writer is not None else 0,
            "timings": {"model_setup_seconds": model_setup_seconds, "inference_seconds": inference_seconds,
                        "source_fingerprint_seconds": fingerprint_seconds},
            "warnings": warnings,
            "limitations": [
                "CONFIRMED means configurable temporal model-evidence rules passed; human review is required and actual accidents are not independently verified.",
                "Repeated accident-like imagery, parked/damaged vehicles, clip cuts, or camera changes can still cause false confirmations; scene physics is not inferred.",
                "Confidence is an uncalibrated model score, not an accident probability.",
                "Real-world unseen-video accuracy is not yet established; no labeled evaluation was performed.",
                "Decoder EOF and video metadata cannot prove absence of hidden corruption.",
            ],
        }
    # Output writer final validation/release has completed before reporting success.
    prepare_result(result, config, fingerprint)
    publish_result(result, source, timeline, config, fingerprint, started=started, progress=progress)
    if callback is not None:
        delivered = CallbackDispatcher((callback,)).publish(result)
        if progress:
            progress(f"Delivered {delivered} local incident callback event(s) after report publication.")
    return result
