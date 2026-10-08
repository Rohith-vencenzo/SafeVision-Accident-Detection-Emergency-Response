"""Version 1.0 result identity and essential contract invariants (stdlib only)."""

import hashlib
import json
import math
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from . import __version__
from .config import ProjectConfig
from .fingerprint import SourceFingerprint

SCHEMA_VERSION = "1.0"
ALGORITHM_VERSION = "temporal_rules_v1"


def incident_identity(result: dict[str, Any], candidate: dict[str, Any]) -> str:
    """Stable across path/clock/run changes for identical media/model/settings/bounds."""
    basis = {
        "algorithm": ALGORITHM_VERSION, "source_sha256": result["source"]["sha256"],
        "checkpoint_sha256": result["model"]["sha256"],
        "accident_classes": {str(key): result["model"]["class_names"].get(key, result["model"]["class_names"].get(str(key)))
                             for key in result["model"]["accident_class_ids"]},
        "inference": {key: result["inference_config"][key] for key in ("confidence", "image_size", "frame_stride")},
        "decision": result["decision_config"],
        "support_frame_bounds": [candidate["support_frames"][0]["frame_index"], candidate["support_frames"][-1]["frame_index"]],
    }
    encoded = json.dumps(basis, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def prepare_result(result: dict[str, Any], config: ProjectConfig, fingerprint: SourceFingerprint) -> None:
    """Enrich the existing temporal result while preserving its observation fields."""
    result.update({
        "schema_version": SCHEMA_VERSION, "project_version": __version__, "phase": 4,
        "analysis_algorithm": ALGORITHM_VERSION, "run_id": f"run_{uuid4().hex}",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "overlay_config": asdict(config.overlay), "reporting_config": asdict(config.reporting),
        "camera_metadata": {"origin": "registered_metadata", **asdict(config.registered_metadata)},
        "artifact_paths": {"run_directory": None, "json_report": None, "human_report": None},
        "retention_policy": {"max_runs": config.reporting.retention_max_runs, "days": config.reporting.retention_days,
                             "scope": "verified_managed_report_bundles", "cleanup": "best_effort_after_publication"},
    })
    result["source"].update({"sha256": fingerprint.sha256, "size_bytes": fingerprint.size_bytes})
    for category in ("incidents", "rejected_candidates", "unresolved_candidates"):
        for candidate in result[category]:
            identity = incident_identity(result, candidate)
            candidate["candidate_id"] = f"cand_{identity}"
            candidate["incident_id"] = f"inc_{identity}" if category == "incidents" else None
            candidate["evidence_frames"] = []
            candidate["evidence_status"] = "pending" if category == "incidents" and config.reporting.enabled and config.reporting.save_evidence else (
                "disabled" if category == "incidents" else "not_applicable"
            )
            candidate["model_reference"] = {"checkpoint_path": result["model"]["path"],
                                            "checkpoint_sha256": result["model"]["sha256"],
                                            "class_names": result["model"]["class_names"]}


def _validate_result(result: dict[str, Any]) -> None:
    """Check required v1 fields, decision consistency, stable IDs, and evidence paths."""
    required = {"schema_version", "run_id", "created_at_utc", "source", "model", "incidents",
                "rejected_candidates", "unresolved_candidates", "artifact_paths", "camera_metadata",
                "inference_config", "decision_config", "overlay_config", "reporting_config", "timings",
                "warnings", "limitations", "accident_detected", "incident_decision", "complete_video_processed",
                "decision_state_history", "decoded_frames", "inferred_frames", "retention_policy"}
    if not required <= result.keys() or result["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Result is missing required v1.0 fields or has an unsupported schema version.")
    if not re.fullmatch(r"run_[0-9a-f]{32}", result["run_id"]):
        raise ValueError("Invalid run_id.")
    for digest in (result["source"]["sha256"], result["model"]["sha256"]):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Source/model identity requires a complete SHA-256 digest.")
    if result["model"]["task"] != "detect":
        raise ValueError("The result must reference the verified detection task.")
    artifact_values = list(result["artifact_paths"].values())
    if set(result["artifact_paths"]) != {"run_directory", "json_report", "human_report"} or not (
        all(value is None for value in artifact_values) or all(isinstance(value, str) and value for value in artifact_values)
    ):
        raise ValueError("Artifact paths must describe a complete bundle or all be null.")
    expected = (True, "confirmed_incident") if result["incidents"] else (
        (False, "no_confirmed_incident") if result["complete_video_processed"] else (None, "inconclusive")
    )
    if result["accident_detected"] is not expected[0] or result["incident_decision"] != expected[1]:
        raise ValueError("Video decision does not match confirmation/coverage evidence.")
    if result["camera_metadata"]["origin"] != "registered_metadata" or set(result["camera_metadata"]) != {"origin", "camera_id", "location_label"}:
        raise ValueError("Camera metadata must be explicitly registered labels, without invented location fields.")
    ids: set[str] = set()
    for category in ("incidents", "rejected_candidates", "unresolved_candidates"):
        for candidate in result[category]:
            if not re.fullmatch(r"cand_[0-9a-f]{24}", candidate["candidate_id"]) or candidate["candidate_id"] != f"cand_{incident_identity(result, candidate)}":
                raise ValueError("Candidate identity does not match content and temporal support bounds.")
            if candidate["candidate_id"] in ids:
                raise ValueError("Candidate identities must be unique within a run.")
            ids.add(candidate["candidate_id"])
            if candidate["supporting_frame_count"] != len(candidate["support_frames"]) or candidate["observed_frame_count"] < candidate["supporting_frame_count"]:
                raise ValueError("Candidate evidence counts are inconsistent.")
            support_indices = [item["frame_index"] for item in candidate["support_frames"]]
            if (not support_indices or any(type(index) is not int or not 0 <= index < result["decoded_frames"] for index in support_indices)
                    or support_indices != sorted(set(support_indices))):
                raise ValueError("Supporting frames must be distinct processed source frames in temporal order.")
            if candidate["strongest_frame"]["frame_index"] not in support_indices:
                raise ValueError("Strongest evidence must be one of the actual supporting frames.")
            if category == "incidents":
                if not re.fullmatch(r"inc_[0-9a-f]{24}", candidate["incident_id"]) or candidate["state"] != "CONFIRMED" or candidate["supporting_frame_count"] < 2:
                    raise ValueError("An incident requires stable identity and multi-frame CONFIRMED evidence.")
                if candidate["incident_id"] != f"inc_{incident_identity(result, candidate)}":
                    raise ValueError("Incident identity does not match source/model/settings/support bounds.")
                proof = candidate["confirmation_evidence"]
                thresholds = result["decision_config"]
                if not isinstance(proof, dict) or not (
                    proof["supporting_frames"] >= thresholds["min_support_frames"]
                    and proof["support_span_seconds"] + 1e-9 >= thresholds["min_support_span_seconds"]
                    and thresholds["min_positive_ratio"] <= proof["positive_ratio"] + 1e-9 <= 1 + 1e-9
                    and proof["current_consecutive_support_frames"] >= thresholds["min_consecutive_frames"]
                    and proof["maximum_sample_gap_seconds"] <= thresholds["max_sample_gap_seconds"] + 1e-9
                    and proof["confirmed_at_seconds"] == candidate["confirmed_at_seconds"]
                    and candidate["start_seconds"] <= candidate["confirmed_at_seconds"] <= candidate["end_seconds"]
                ):
                    raise ValueError("Confirmed incidents require the recorded passing temporal-window evidence.")
            elif candidate["incident_id"] is not None or candidate["evidence_frames"]:
                raise ValueError("Rejected/unresolved candidates cannot be published as confirmed incident evidence.")
            if candidate["evidence_status"] not in {"saved", "partial", "unavailable", "disabled", "not_applicable"}:
                raise ValueError("Evidence must be resolved before publishing the result.")
            roles: set[str] = set()
            for evidence in candidate["evidence_frames"]:
                role = evidence["role"]
                path = PurePosixPath(evidence["path"])
                if role not in {"before", "strongest", "after"} or role in roles or path.is_absolute() or ".." in path.parts:
                    raise ValueError("Evidence roles/paths must be unique, bounded, and relative to the report bundle.")
                if evidence["path"] != f"evidence/{candidate['incident_id']}/{role}.jpg":
                    raise ValueError("Evidence path must belong to its own incident directory.")
                roles.add(role)
                if type(evidence["frame_index"]) is not int or not 0 <= evidence["frame_index"] < result["decoded_frames"]:
                    raise ValueError("Evidence must reference an actually processed source frame.")
                if not math.isfinite(evidence["timestamp_seconds"]) or evidence["timestamp_seconds"] < 0:
                    raise ValueError("Evidence timestamps must be finite video times.")
                if evidence["image_kind"] != "source_frame" or not re.fullmatch(r"[0-9a-f]{64}", evidence["sha256"]):
                    raise ValueError("Evidence must identify a hashed original source-frame JPEG.")
                anchor = candidate["strongest_frame"]
                if role == "strongest" and evidence["frame_index"] != anchor["frame_index"]:
                    raise ValueError("Strongest image must match the strongest supporting frame index.")
                if role == "before" and evidence["frame_index"] >= anchor["frame_index"] or role == "after" and evidence["frame_index"] <= anchor["frame_index"]:
                    raise ValueError("Before/after evidence must not duplicate or misorder the strongest frame.")
                if abs(evidence["timestamp_seconds"] - anchor["timestamp_seconds"] - evidence["offset_from_strongest_seconds"]) > 1e-6:
                    raise ValueError("Evidence offsets must match actual video timestamps.")
            if len(roles) > 3:
                raise ValueError("At most three evidence images may be saved per incident.")
            if roles and result["artifact_paths"]["run_directory"] is None:
                raise ValueError("Saved evidence requires a published report bundle path.")
            if candidate["evidence_status"] == "saved" and "strongest" not in roles:
                raise ValueError("Saved evidence must include the actual strongest source frame.")
            if candidate["evidence_status"] in {"disabled", "unavailable", "not_applicable"} and roles:
                raise ValueError("Evidence status must match the presence of saved images.")
    # Reject any nonfinite value anywhere rather than emit invalid machine JSON.
    json.dumps(result, allow_nan=False)


def validate_result(result: dict[str, Any]) -> None:
    """Raise ValueError for malformed/unsupported results and semantic contradictions."""
    try:
        _validate_result(result)
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f"Malformed v1.0 result structure: {exc}.") from exc
