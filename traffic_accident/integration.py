"""Future-service callback seam and local simulation-only alert sink.

There is intentionally no HTTP, socket, SMS, phone, cloud, or emergency-dispatch
implementation here. Callbacks run only after the local report bundle has been
published successfully. A future adapter can consume IncidentEvent without
coupling the detector, temporal engine, video I/O, or reporting implementation.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .atomic import atomic_write


class IncidentCallback(Protocol):
    """Interface a future local service adapter may implement."""

    def on_incident(self, event: "IncidentEvent") -> None:
        """Consume a human-review-required incident event after publication."""
        ...


@dataclass(frozen=True)
class IncidentEvent:
    """Minimal future-integration payload derived from a validated result."""

    contract_version: str
    event_type: str
    run_id: str
    incident_id: str
    source_sha256: str
    checkpoint_sha256: str
    support_start_seconds: float
    support_end_seconds: float
    confirmed_at_seconds: float
    strongest_frame_index: int
    confidence_evidence: dict[str, Any]
    evidence_frames: tuple[dict[str, Any], ...]
    camera_metadata: dict[str, Any]
    human_review_required: bool = True
    human_verified: bool = False
    emergency_action_authorized: bool = False

    @classmethod
    def from_result(cls, result: dict[str, Any], incident: dict[str, Any]) -> "IncidentEvent":
        """Build only from a validated confirmed incident and v1 result."""
        if result.get("schema_version") != "1.0" or incident.get("state") != "CONFIRMED":
            raise ValueError("Incident callbacks require a validated v1.0 CONFIRMED result candidate.")
        return cls(
            contract_version="1.0", event_type="incident_temporal_confirmation",
            run_id=result["run_id"], incident_id=incident["incident_id"],
            source_sha256=result["source"]["sha256"], checkpoint_sha256=result["model"]["sha256"],
            support_start_seconds=incident["start_seconds"], support_end_seconds=incident["end_seconds"],
            confirmed_at_seconds=incident["confirmed_at_seconds"],
            strongest_frame_index=incident["strongest_frame"]["frame_index"],
            confidence_evidence=dict(incident["confidence_evidence"]),
            evidence_frames=tuple(dict(item) for item in incident["evidence_frames"]),
            camera_metadata={"origin": "registered_metadata",
                             "camera_id": result["camera_metadata"].get("camera_id"),
                             "location_label": result["camera_metadata"].get("location_label")},
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe payload with explicit safety gates."""
        return {
            "contract_version": self.contract_version, "event_type": self.event_type,
            "run_id": self.run_id, "incident_id": self.incident_id,
            "source_sha256": self.source_sha256, "checkpoint_sha256": self.checkpoint_sha256,
            "support_start_seconds": self.support_start_seconds, "support_end_seconds": self.support_end_seconds,
            "confirmed_at_seconds": self.confirmed_at_seconds,
            "strongest_frame_index": self.strongest_frame_index,
            "confidence_evidence": self.confidence_evidence,
            "evidence_frames": list(self.evidence_frames), "camera_metadata": self.camera_metadata,
            "human_review_required": self.human_review_required,
            "human_verified": self.human_verified,
            "emergency_action_authorized": self.emergency_action_authorized,
        }


class CallbackDispatcher:
    """Fan out confirmed events to explicitly registered local callbacks."""

    def __init__(self, callbacks: Sequence[IncidentCallback] = ()) -> None:
        self.callbacks = tuple(callbacks)

    def publish(self, result: dict[str, Any]) -> int:
        """Emit one event per confirmed incident; return number delivered."""
        delivered = 0
        for incident in result.get("incidents", []):
            event = IncidentEvent.from_result(result, incident)
            for callback in self.callbacks:
                callback.on_incident(event)
                delivered += 1
        return delivered


class SimulationAlertSink:
    """Write local JSONL simulation records; never sends a notification or network request."""

    mode = "simulation_only"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []

    def on_incident(self, event: IncidentEvent) -> None:
        """Append a local demonstration record with no external side effect."""
        payload = {"simulation": True, "mode": self.mode, "event": event.as_dict()}
        self.events.append(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing: list[str] = []
        if self.path.exists():
            existing = self.path.read_text(encoding="utf-8").splitlines()
        content = "\n".join(existing + [json.dumps(payload, sort_keys=True, allow_nan=False)]) + "\n"
        atomic_write(self.path, content.encode("utf-8"))
