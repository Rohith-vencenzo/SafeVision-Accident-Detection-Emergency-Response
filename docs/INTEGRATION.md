# Future integration contract — Phase 5

This project remains local-first. There is no FastAPI server, HTTP client, socket,
SMS/phone provider, Android code, cloud deployment, credential handling, or police/
ambulance dispatch implementation. The supplied simulation sink writes local JSONL
only. Do not connect it to emergency action.

## Callback seam

`traffic_accident.integration.IncidentCallback` is the future adapter boundary:

```python
class IncidentCallback(Protocol):
    def on_incident(self, event: IncidentEvent) -> None: ...
```

`analyze_video(..., callback=callback)` invokes the callback **after** the local
v1.0 report/evidence bundle has passed validation and has been published. The
callback receives one immutable `IncidentEvent` per confirmed candidate. The
detector, video reader/writer, temporal engine, evidence selector, and reporting
bundle do not know about HTTP or mobile services.

Callbacks are not invoked for rejected or unresolved candidates. A callback failure
is surfaced to the local caller after the report was already safely published; a
future service adapter must make its own retry/idempotency decision using
`run_id` + `incident_id`.

## Future API payload

The future transport may serialize `IncidentEvent.as_dict()` as an event payload:

```json
{
  "contract_version": "1.0",
  "event_type": "incident_temporal_confirmation",
  "run_id": "run_<32 hex>",
  "incident_id": "inc_<24 hex>",
  "source_sha256": "<64 hex>",
  "checkpoint_sha256": "<64 hex>",
  "support_start_seconds": 0.5,
  "support_end_seconds": 3.5,
  "confirmed_at_seconds": 1.0,
  "strongest_frame_index": 75,
  "confidence_evidence": {
    "minimum": 0.65,
    "maximum": 0.90,
    "mean": 0.84,
    "meaning": "uncalibrated per-frame model scores"
  },
  "evidence_frames": [
    {"role": "strongest", "path": "evidence/inc_<24 hex>/strongest.jpg", "frame_index": 75, "timestamp_seconds": 2.5}
  ],
  "camera_metadata": {"origin": "registered_metadata", "camera_id": null, "location_label": null},
  "human_review_required": true,
  "human_verified": false,
  "emergency_action_authorized": false
}
```

The payload is model evidence and local artifact references. It is not a diagnosis,
injury assessment, calibrated probability, GPS fix, legal conclusion, or command to
contact emergency services. Evidence paths are relative to the published bundle;
a future service must authenticate/authorize any artifact retrieval separately.
The payload has no secret, credential, or raw personal-data field.

## Required future safety gates

Any future service must:

1. Authenticate and authorize the caller/receiver independently of this prototype.
2. Treat `incident_id`/`run_id` as idempotency keys and reject malformed schemas.
3. Verify the checkpoint/source hashes and bundle/evidence availability as needed.
4. Display that temporal rules passed, not that an accident is proven.
5. Require a human to review the source video/evidence and explicitly verify an
   alert **before any emergency action**.
6. Never infer injury, medical status, vehicle physics, location, or severity from
   fields that are not explicitly registered/supplied.
7. Record acknowledgement/review/audit state separately from this AI result.
8. Keep notification failures from corrupting or rewriting the local result bundle.

Emergency action must not be automatic from this event. Human verification is a
mandatory future gate, not optional UI text.

## Simulation-only implementation

`SimulationAlertSink` implements the callback locally:

```bash
python main.py --source "test_clip/accident_demo.mp4" --no-show --simulate-alerts --json
```

It writes one JSON object per confirmed event to a local JSONL file (default
`logs/simulation-alerts.jsonl` or `--simulation-alert-log <path>`). It uses no
network-related Python module or OS operation, sends nothing externally, and
sets `simulation: true`, `human_review_required: true`, and
`emergency_action_authorized: false`. It is suitable for classroom demonstrations
of callback flow only.

## Scope boundary

Phase 5 implements and tests this local callback/simulation seam only. FastAPI,
mobile alarms, real calls/SMS, police/ambulance dispatch, credentials, cloud
deployment, and network requests are out of scope. Phase 6 is reserved for
whole-video evaluation, robustness, local benchmarking, documentation handoff,
and any measured accuracy results.
