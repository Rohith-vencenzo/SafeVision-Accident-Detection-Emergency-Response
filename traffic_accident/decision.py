"""Timestamp-based temporal evidence rules; confirmation is not ground truth.

This module needs no OpenCV, Ultralytics, NumPy, weights, or footage. Input is one
observation per inferred frame, including empty detections; skipped frames only
advance the clock. The actual verified accident class mapping is supplied by caller.
"""

import math
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import fmean
from typing import Any, Mapping, Sequence

from .config import DecisionConfig
from .observations import Detection

EPSILON = 1e-9


class DecisionState(StrEnum):
    NORMAL = "NORMAL"
    POSSIBLE = "POSSIBLE"
    VERIFYING = "VERIFYING"
    CONFIRMED = "CONFIRMED"
    FALSE_ALARM = "FALSE_ALARM"


@dataclass(frozen=True)
class SupportFrame:
    frame_index: int
    timestamp_seconds: float
    timestamp_source: str
    detections: tuple[Detection, ...]

    @property
    def confidence(self) -> float:
        return max(item.confidence for item in self.detections)

    def as_dict(self) -> dict[str, Any]:
        return {"frame_index": self.frame_index, "timestamp_seconds": self.timestamp_seconds,
                "timestamp_source": self.timestamp_source, "confidence": self.confidence,
                "detections": [item.as_dict() for item in self.detections]}


@dataclass
class _Candidate:
    number: int
    state: DecisionState = DecisionState.POSSIBLE
    supports: list[SupportFrame] = field(default_factory=list)
    window: deque[tuple[float, bool]] = field(default_factory=deque)
    observed_frames: int = 0
    negative_frames: int = 0
    weak_frames: int = 0
    current_consecutive_run: int = 0
    longest_consecutive_run: int = 0
    best_window_support_count: int = 0
    best_window_support_span_seconds: float = 0.0
    confirmation: dict[str, Any] | None = None
    last_support_window_metrics: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


class TemporalDecisionEngine:
    """Group supports in clip time and latch confirmation only after all rules pass."""

    def __init__(self, accident_class_names: Mapping[int, str], options: DecisionConfig) -> None:
        options.validate()
        if (not accident_class_names or any(type(key) is not int or key < 0 or not isinstance(value, str)
                                           or not value for key, value in accident_class_names.items())):
            raise ValueError("Provide the verified accident-related class mapping from the checkpoint.")
        self.class_names = dict(accident_class_names)
        self.options = options
        self.state = DecisionState.NORMAL
        self.state_history: list[dict[str, Any]] = [
            {"state": "NORMAL", "timestamp_seconds": 0.0, "candidate_number": None, "reason": "engine_initialized"}
        ]
        self.confirmed: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []
        self.unresolved: list[dict[str, Any]] = []
        self._active: _Candidate | None = None
        self._next_number = 1
        self._clock = 0.0
        self._last_sample_time: float | None = None
        self._last_frame_index: int | None = None
        self._finished = False
        self.observed_frames = 0
        self.frames_below_support_threshold = 0
        self.frames_with_only_irrelevant_classes = 0
        self.sampling_gap_breaks = 0

    def _transition(self, state: DecisionState, timestamp: float, reason: str) -> None:
        candidate = self._active
        entry = {"state": state.value, "timestamp_seconds": timestamp,
                 "candidate_number": candidate.number if candidate else None, "reason": reason}
        self.state = state
        self.state_history.append(entry)
        if candidate is not None and state != DecisionState.NORMAL:
            candidate.state = state
            candidate.history.append(dict(entry))

    def snapshot(self) -> dict[str, Any]:
        """Expose live prototype state/counts without leaking mutable candidates."""
        return {
            "state": self.state.value,
            "candidate_number": self._active.number if self._active else None,
            "confirmed_count": len(self.confirmed) + int(self.state == DecisionState.CONFIRMED),
            "rejected_count": len(self.rejected),
        }

    def advance(self, timestamp_seconds: float) -> None:
        """Advance on decoded frames without treating unsampled frames as negatives."""
        if self._finished:
            raise ValueError("The decision engine was finalized; create a new engine for another clip.")
        if (isinstance(timestamp_seconds, bool) or not math.isfinite(timestamp_seconds)
                or timestamp_seconds < 0 or timestamp_seconds < self._clock - EPSILON):
            raise ValueError("Video timestamps must be finite, nonnegative, and monotonic.")
        self._clock = timestamp_seconds
        if self._active and timestamp_seconds - self._active.supports[-1].timestamp_seconds > self.options.event_gap_seconds + EPSILON:
            self._close(timestamp_seconds, "gap_timeout", complete=True)

    def observe(self, frame_index: int, timestamp_seconds: float, detections: Sequence[Detection],
                *, timestamp_source: str = "decoder") -> DecisionState:
        """Ingest one inferred frame. Multiple boxes on one frame count as one support."""
        if type(frame_index) is not int or frame_index < 0 or (self._last_frame_index is not None and frame_index <= self._last_frame_index):
            raise ValueError("Observed frame indices must be nonnegative and strictly increasing.")
        if self._last_sample_time is not None and timestamp_seconds <= self._last_sample_time + EPSILON:
            raise ValueError("Inferred-frame timestamps must be strictly increasing.")
        if timestamp_source not in ("decoder", "fps_fallback"):
            raise ValueError("timestamp_source must be decoder or fps_fallback.")
        relevant = []
        for detection in detections:
            if detection.class_id not in self.class_names:
                continue
            if detection.class_name != self.class_names[detection.class_id]:
                raise ValueError("Observation label does not match the verified checkpoint class mapping.")
            if not math.isfinite(detection.confidence) or not 0 <= detection.confidence <= 1:
                raise ValueError("Detection confidence must be a finite model score in [0, 1].")
            relevant.append(detection)
        self.advance(timestamp_seconds)
        self._last_sample_time = timestamp_seconds
        self._last_frame_index = frame_index
        self.observed_frames += 1
        supports = tuple(item for item in relevant if item.confidence >= self.options.support_confidence)
        if relevant and not supports:
            self.frames_below_support_threshold += 1
        if detections and not relevant:
            self.frames_with_only_irrelevant_classes += 1
        if supports and self._active is None:
            self._active = _Candidate(self._next_number)
            self._next_number += 1
            self._transition(DecisionState.POSSIBLE, timestamp_seconds, "first_qualifying_frame")
        candidate = self._active
        if candidate is None:
            return self.state
        candidate.observed_frames += 1
        if supports:
            candidate.supports.append(SupportFrame(frame_index, timestamp_seconds, timestamp_source, supports))
        else:
            candidate.negative_frames += 1
            candidate.weak_frames += bool(relevant)
        large_sampling_gap = bool(candidate.window and timestamp_seconds - candidate.window[-1][0] > self.options.max_sample_gap_seconds + EPSILON)
        if large_sampling_gap:
            # No extrapolated continuity through a large sampling gap.
            candidate.window.clear()
            self.sampling_gap_breaks += 1
        candidate.current_consecutive_run = (candidate.current_consecutive_run + 1 if not large_sampling_gap else 1) if supports else 0
        candidate.longest_consecutive_run = max(candidate.longest_consecutive_run, candidate.current_consecutive_run)
        candidate.window.append((timestamp_seconds, bool(supports)))
        while candidate.window and timestamp_seconds - candidate.window[0][0] > self.options.window_seconds + EPSILON:
            candidate.window.popleft()
        metrics = self._window_metrics(candidate)
        if supports:
            candidate.last_support_window_metrics = metrics
        candidate.best_window_support_count = max(candidate.best_window_support_count, metrics["supporting_frames"])
        candidate.best_window_support_span_seconds = max(candidate.best_window_support_span_seconds, metrics["support_span_seconds"])
        if candidate.state == DecisionState.POSSIBLE and metrics["current_consecutive_support_frames"] >= 2:
            self._transition(DecisionState.VERIFYING, timestamp_seconds, "multiple_continuous_support_frames")
        if candidate.state != DecisionState.CONFIRMED and supports and self._requirements_pass(metrics):
            candidate.confirmation = {**metrics, "confirmed_at_seconds": timestamp_seconds}
            self._transition(DecisionState.CONFIRMED, timestamp_seconds, "all_temporal_requirements_passed")
        return self.state

    def _window_metrics(self, candidate: _Candidate) -> dict[str, Any]:
        positives = [timestamp for timestamp, positive in candidate.window if positive]
        consecutive = 0
        for _, positive in reversed(candidate.window):
            if not positive:
                break
            consecutive += 1
        times = [timestamp for timestamp, _ in candidate.window]
        return {
            "window_start_seconds": times[0], "window_end_seconds": times[-1],
            "observed_frames": len(times), "supporting_frames": len(positives),
            "positive_ratio": len(positives) / len(times),
            "support_span_seconds": positives[-1] - positives[0] if positives else 0.0,
            "current_consecutive_support_frames": consecutive,
            "maximum_sample_gap_seconds": max((b - a for a, b in zip(times, times[1:])), default=0.0),
        }

    def _requirements_pass(self, metrics: Mapping[str, Any]) -> bool:
        options = self.options
        return (metrics["supporting_frames"] >= options.min_support_frames
                and metrics["support_span_seconds"] + EPSILON >= options.min_support_span_seconds
                and metrics["positive_ratio"] + EPSILON >= options.min_positive_ratio
                and metrics["current_consecutive_support_frames"] >= options.min_consecutive_frames
                and metrics["maximum_sample_gap_seconds"] <= options.max_sample_gap_seconds + EPSILON)

    def _close(self, timestamp: float, reason: str, *, complete: bool) -> None:
        candidate = self._active
        if candidate is None:
            return
        failed_rules = []
        if candidate.confirmation is None:
            if len(candidate.supports) == 1:
                failed_rules.append("isolated_single_frame")
            if candidate.best_window_support_count < self.options.min_support_frames:
                failed_rules.append("insufficient_window_support_frames")
            if candidate.best_window_support_span_seconds + EPSILON < self.options.min_support_span_seconds:
                failed_rules.append("insufficient_support_span")
            if candidate.longest_consecutive_run < self.options.min_consecutive_frames:
                failed_rules.append("insufficient_continuity")
            failed_rules.append("temporal_requirements_never_passed_together")
            metrics = candidate.last_support_window_metrics
            if metrics is not None and metrics["positive_ratio"] + EPSILON < self.options.min_positive_ratio:
                failed_rules.append("last_support_window_positive_ratio_below_threshold")
            if metrics is not None and metrics["current_consecutive_support_frames"] < self.options.min_consecutive_frames:
                failed_rules.append("last_support_window_continuity_below_threshold")
            if complete:
                self._transition(DecisionState.FALSE_ALARM, timestamp, ",".join(failed_rules))
            else:
                failed_rules.append("processing_stopped_before_candidate_resolved")
        strongest = max(candidate.supports, key=lambda item: item.confidence)
        scores = [item.confidence for item in candidate.supports]
        record = {
            "candidate_number": candidate.number,
            "state": candidate.state.value,
            "start_seconds": candidate.supports[0].timestamp_seconds,
            "end_seconds": candidate.supports[-1].timestamp_seconds,
            "closed_at_seconds": timestamp, "closure_reason": reason,
            "confirmed_at_seconds": candidate.confirmation["confirmed_at_seconds"] if candidate.confirmation else None,
            "confirmation_evidence": candidate.confirmation,
            "last_support_window_metrics": candidate.last_support_window_metrics,
            "supporting_frame_count": len(candidate.supports), "observed_frame_count": candidate.observed_frames,
            "negative_or_weak_frame_count": candidate.negative_frames, "weak_frame_count": candidate.weak_frames,
            "longest_consecutive_support_frames": candidate.longest_consecutive_run,
            "max_support_gap_seconds": max((b.timestamp_seconds - a.timestamp_seconds
                                             for a, b in zip(candidate.supports, candidate.supports[1:])), default=0.0),
            "confidence_evidence": {"minimum": min(scores), "maximum": max(scores), "mean": fmean(scores),
                                    "meaning": "uncalibrated per-frame model scores"},
            "strongest_frame": strongest.as_dict(),
            "support_frames": [item.as_dict() for item in candidate.supports],
            "timestamp_estimates_used": any(item.timestamp_source == "fps_fallback" for item in candidate.supports),
            "ended_at_processing_boundary": reason != "gap_timeout",
            "processing_boundary_is_partial": not complete,
            "diagnostic_reasons": failed_rules,
            "state_history": candidate.history,
        }
        if candidate.confirmation is not None:
            self.confirmed.append(record)
        elif complete:
            self.rejected.append(record)
        else:
            self.unresolved.append(record)
        self._active = None
        self._transition(DecisionState.NORMAL, timestamp, "candidate_closed")

    def finish(self, timestamp_seconds: float, *, complete: bool, reason: str = "clip_end") -> dict[str, Any]:
        """Flush clip-end events; partial pending candidates are unresolved, not false alarms."""
        self.advance(timestamp_seconds)
        self._close(timestamp_seconds, reason, complete=complete)
        self._finished = True
        detected = True if self.confirmed else (False if complete else None)
        decision = "confirmed_incident" if self.confirmed else ("no_confirmed_incident" if complete else "inconclusive")
        return {
            "accident_detected": detected, "incident_decision": decision,
            "confirmation_meaning": "Temporal model-evidence rules passed; not independent verification or calibrated accident probability.",
            "incidents": self.confirmed, "rejected_candidates": self.rejected, "unresolved_candidates": self.unresolved,
            "decision_state_history": self.state_history,
            "decision_diagnostics": {"observed_frames": self.observed_frames,
                                     "frames_below_support_threshold": self.frames_below_support_threshold,
                                     "frames_with_only_irrelevant_classes": self.frames_with_only_irrelevant_classes,
                                     "sampling_gap_breaks": self.sampling_gap_breaks},
        }
