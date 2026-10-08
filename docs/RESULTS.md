# Versioned result and artifact contract — v1.0

This contract describes the Phase 4 output of the local decision-support
prototype. A `CONFIRMED` result means that configured temporal model-evidence
rules passed; it is not independently verified ground truth, a calibrated
probability, an injury/medical assessment, or an emergency dispatch decision.

The machine-readable reference is [`result.schema.json`](result.schema.json).
Runtime validation additionally checks semantic relationships that JSON Schema
alone cannot express, such as stable IDs, temporal evidence thresholds, source
frame ordering, evidence image hashes, and decision/coverage consistency.

## Result fields

Every returned result and published `result.json` includes:

- `schema_version: "1.0"`, project/algorithm versions, unique `run_id`, UTC
  creation time, and timing scope.
- Source path, dimensions/FPS assumptions, frame counts, source SHA-256/size,
  and processing termination/coverage.
- Checkpoint path/task/actual classes/accident IDs/SHA-256, inference and
  temporal decision settings.
- `accident_detected` and `incident_decision`:
  - `true` / `confirmed_incident` if any candidate passed temporal rules;
  - `false` / `no_confirmed_incident` only after complete clip processing with
    no confirmations;
  - `null` / `inconclusive` for partial coverage without a confirmation.
- Original frame observations with boxes/class IDs/names, model scores and
  decoder/FPS timestamp provenance.
- Confirmed `incidents`, `rejected_candidates`, and `unresolved_candidates`.
  Candidate IDs are deterministic within the stable media/model/settings/evidence
  identity; incident IDs are deterministic stable `inc_<24 hex>` values. Run IDs
  are unique per execution. Candidate numbers are local to one run.
- Full state history, score evidence, temporal window proof, strongest supporting
  frame, support frames, rejection diagnostics, warnings, limitations, and actual
  processing timing fields.
- `camera_metadata` whose origin is always `registered_metadata`. Camera/location
  values are optional user-registered labels. The application never generates
  GPS, address, or location from the video.

## Incident evidence

Only confirmed candidates receive evidence images. At most three source-frame JPEGs
are selected:

- `before`: nearest processed source frame before the strongest model-evidence frame;
- `strongest`: exactly the recorded strongest supporting source frame;
- `after`: nearest processed source frame after it.

The default requested context is one video second. A clip boundary, partial limit,
or unavailable frame may omit a role or mark `context_limited: true`. These labels
do not assert pre-impact/post-impact physics. Rejected/unresolved candidates never
receive incident image paths. Images contain original decoded pixels, not overlay
text, and each has a SHA-256 verified against the bundle.

## Published bundle

When reporting is enabled, the final directory is published only after all result,
Markdown, evidence, and completion-manifest checks pass:

```text
incidents/run_<32-hex>/
  result.json
  report.md
  COMPLETE.json
  evidence/inc_<24-hex>/
    before.jpg       # when available
    strongest.jpg
    after.jpg        # when available
```

`COMPLETE.json` identifies traffic-accident-managed bundles, records schema/run/
creation data, inventories every generated file/directory, and stores file hashes.
Retention will only remove bundles whose marker, inventory, result identity, and
all file hashes still verify. Staging names begin with `.` and are not managed.

The bundle's Markdown report is human-readable and links evidence images with
source/frame/timestamp/limited-context annotations. It includes source/checkpoint
identity, registered metadata, candidate state/evidence summaries, timing scope,
warnings, limitations, and retention policy.

The annotated video is a separate optional named artifact. It has current state
and temporal status in the overlay:

- `NORMAL`: idle, not a safety assessment;
- `POSSIBLE` / `VERIFYING`: verification pending — incident not confirmed;
- `CONFIRMED`: rule-confirmed — human review required;
- `FALSE_ALARM` is retained in reports/history and is not a live physical claim.

Frame class/score boxes remain labeled as model scores. A skipped frame does not
display stale detections. JSON/report publication does not claim that every output
video is available when `--no-save` is used.

## Safe writes and retention

Files are written into a same-filesystem hidden staging directory. Content files
are flushed and atomically replaced; the completed staging directory is renamed
into its final run path. Existing files are never overwritten. Source mutation,
encoding errors, semantic contract errors, report errors, and rename failures
prevent final publication and clean the new staging area when possible.

`--retain-runs N` and `--retention-days D` are opt-in. The cleanup pass preserves
the current run, source/model protected paths, unmanaged folders, symlinks, files
with user additions/modifications, malformed markers, and altered manifests. It
reports skipped/error counts rather than silently deleting uncertain data. Named
annotated videos are outside this policy. A sudden process or machine crash can
leave an unlisted hidden staging directory; it is not published or eligible for
cleanup without a valid completion marker.

## Compatibility and limitations

The contract does not expose secrets, credentials, or inferred personal/location
data. It deliberately does not include medical diagnosis, injury status, police/
ambulance dispatch, phone calls, SMS, network notifications, or backend/mobile
integration. Those are outside this local phase. The contract is designed so a
future callback/API can consume the result after human verification without
changing the core detector/decision logic.

Evidence and reports improve inspectability, not detection accuracy. No labeled
held-out video test set was supplied. Do not interpret the generated sample counts
as precision, recall, false-alarm rates, calibrated probabilities, or proof of
actual accidents.
