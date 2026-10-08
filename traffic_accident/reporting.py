"""Transactional report/evidence bundles and versioned JSON result publication."""

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Sequence

from .atomic import atomic_write
from .config import ProjectConfig
from .contracts import validate_result
from .errors import SetupError
from .evidence import FrameStamp, save_evidence
from .fingerprint import SourceFingerprint
from .retention import apply_retention
from .summaries import human_summary


def render_report(result: dict[str, Any]) -> str:
    """Render inspectable Markdown with local image links, even for zero incidents."""
    camera = result["camera_metadata"]
    lines = ["# Traffic-accident video analysis", "", f"Schema: **{result['schema_version']}** · Run: `{result['run_id']}`",
             f"Created (UTC): {result['created_at_utc']}", "",
             "**Decision-support prototype: temporal confirmation requires human review. Real-world accuracy is not established.**",
             "", "## Summary", "", "```text", human_summary(result), "```", "",
             "## Source and checkpoint", "",
             f"- Source: `{result['source']['path']}`",
             f"- Source SHA-256: `{result['source']['sha256']}`",
             f"- Checkpoint: `{result['model']['path']}`",
             f"- Checkpoint SHA-256: `{result['model']['sha256']}`",
             f"- Actual task/classes: `{result['model']['task']}` / `{result['model']['class_names']}`", "",
             "## Registered metadata", "",
             f"- Camera ID: {camera['camera_id'] or 'Not registered'}",
             f"- Location label: {camera['location_label'] or 'Not registered'}",
             "- These are user-registered labels, not location inferred from pixels. No GPS is fabricated.", ""]
    for incident in result["incidents"]:
        lines.extend([f"## Incident `{incident['incident_id']}`", "",
                      f"- Support interval: {incident['start_seconds']:.3f}–{incident['end_seconds']:.3f} video seconds.",
                      f"- Temporal confirmation: {incident['confirmed_at_seconds']:.3f}s; supports: {incident['supporting_frame_count']}.",
                      f"- Strongest frame: {incident['strongest_frame']['frame_index']} at {incident['strongest_frame']['timestamp_seconds']:.3f}s.",
                      f"- Model score summary (uncalibrated): `{incident['confidence_evidence']}`.",
                      f"- Confirmation evidence: `{incident['confirmation_evidence']}`.",
                      f"- State history: `{' → '.join(entry['state'] for entry in incident['state_history'])}`.",
                      f"- Closure: {incident['closure_reason']}; evidence: {incident['evidence_status']}.", ""])
        for evidence in incident["evidence_frames"]:
            lines.extend([f"### {evidence['role'].capitalize()} — {evidence['timestamp_seconds']:.3f}s / frame {evidence['frame_index']}", "",
                          f"Original source frame; limited context: {evidence['context_limited']}.", "",
                          f"![{evidence['role']} source frame](<{evidence['path']}>)", ""])
    lines.extend(["## Rejected and unresolved candidates", ""])
    for category in ("rejected_candidates", "unresolved_candidates"):
        for candidate in result[category]:
            lines.append(f"- `{candidate['candidate_id']}` ({candidate['state']}), {candidate['start_seconds']:.3f}–{candidate['end_seconds']:.3f}s: {', '.join(candidate['diagnostic_reasons'])}.")
    if not result["rejected_candidates"] and not result["unresolved_candidates"]:
        lines.append("None.")
    lines.extend(["", "## Timing and limitations", "", f"- Timings: `{result['timings']}`.",
                  f"- Timing scope: {result['timing_scope']}"])
    lines.extend(f"- {message}" for message in result["limitations"])
    lines.extend(["", "## Retention policy", "", f"`{result['retention_policy']}`", ""])
    return "\n".join(lines)


def _complete_manifest(stage: Path, result: dict[str, Any]) -> dict[str, Any]:
    files = {}
    directories = []
    for entry in stage.rglob("*"):
        relative = entry.relative_to(stage).as_posix()
        if entry.is_file():
            with entry.open("rb") as stream:
                files[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
        elif entry.is_dir():
            directories.append(relative)
    return {"managed_by": "traffic_accident", "schema_version": result["schema_version"],
            "run_id": result["run_id"], "created_at_utc": result["created_at_utc"],
            "files": dict(sorted(files.items())), "directories": sorted(directories)}


def publish_result(result: dict[str, Any], source: Path, timeline: Sequence[FrameStamp], config: ProjectConfig,
                   fingerprint: SourceFingerprint, *, started: float,
                   progress: Callable[[str], None] | None = None) -> None:
    """Publish one complete bundle via same-filesystem rename; no partial report paths."""
    result["timings"]["evidence_seconds"] = 0.0
    result["timing_scope"] = "Elapsed wall time through analysis and evidence preparation; excludes final report serialization, atomic publication, and retention cleanup."
    if not config.reporting.enabled:
        result["timings"]["elapsed_seconds"] = perf_counter() - started
        validate_result(result)
        return
    stage: Path | None = None
    final = config.incidents / result["run_id"]
    try:
        config.incidents.mkdir(parents=True, exist_ok=True)
        if final.exists():
            raise FileExistsError("run directory already exists")
        stage = Path(tempfile.mkdtemp(prefix=f".{result['run_id']}.stage_", dir=config.incidents))
        result["artifact_paths"] = {"run_directory": str(final), "json_report": str(final / "result.json"),
                                    "human_report": str(final / "report.md")}
        if config.reporting.save_evidence and result["incidents"]:
            evidence_started = perf_counter()
            result["warnings"].extend(save_evidence(source, stage, result["incidents"], timeline, config))
            result["timings"]["evidence_seconds"] = perf_counter() - evidence_started
        fingerprint.verify_unchanged(source)
        result["timings"]["elapsed_seconds"] = perf_counter() - started
        validate_result(result)
        encoded = json.dumps(result, indent=2, allow_nan=False, ensure_ascii=False).encode("utf-8") + b"\n"
        atomic_write(stage / "result.json", encoded)
        atomic_write(stage / "report.md", render_report(result).encode("utf-8"))
        atomic_write(stage / "COMPLETE.json", json.dumps(_complete_manifest(stage, result), indent=2).encode("utf-8") + b"\n")
        stage.rename(final)
    except BaseException as exc:
        if isinstance(exc, SetupError) or not isinstance(exc, Exception):
            raise
        raise SetupError(f"Report/evidence publication failed: {exc}. No incomplete incident bundle was published; a successfully finalized annotated video may still be available.") from exc
    finally:
        if stage is not None and stage.exists():
            try:
                shutil.rmtree(stage)
            except OSError as exc:
                raise SetupError(f"Could not clean unpublished report staging {stage}: {exc}. Inspect/remove this staging directory manually.") from exc
    summary = apply_retention(config.incidents, keep_run_id=result["run_id"],
                              max_runs=config.reporting.retention_max_runs, days=config.reporting.retention_days,
                              protected_paths=(source, config.weights))
    if progress and (config.reporting.retention_max_runs is not None or config.reporting.retention_days is not None):
        progress(f"Retention: removed {summary['removed']} verified bundles; skipped {summary['skipped']} unmanaged/modified bundles; cleanup errors {summary['errors']}.")
