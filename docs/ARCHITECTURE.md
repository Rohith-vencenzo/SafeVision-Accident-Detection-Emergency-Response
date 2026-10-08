# Architecture and dependency plan

## Current scope: Phases 1–6

`Accident_Detection/` is the project root. Existing `TASK.md`, model exports,
metadata, and footage are preserved. There was no existing application code.

- `main.py`: thin entry point.
- `traffic_accident/cli.py`: setup diagnostics and actionable errors.
- `config.py`: immutable validated configuration; paths resolve from the root,
  independently of the current working directory. Configuration paths cannot
  escape the project or mix generated artifacts with model/video inputs.
- `checkpoint.py`: local-file checks, SHA-256, and public `YOLO.task` / `YOLO.names`
  inspection. Resolve accident IDs by configured, verified names, never a guessed ID.
- `runtime.py`: project-local dependency settings/cache paths and offline mode.
- `diagnostics.py`: virtual environment, library initialization, native NMS check,
  and actual PyTorch device availability.
- `errors.py`: shared actionable setup exceptions.
- `observations.py`: detector-independent typed box/class/score observations.
- `detector.py`: reusable adapter, verified class filtering, explicit device checks;
  shares `load_checkpoint` with diagnostics and loads once per processing run.
- `video.py`: checked local reader and output writer, overwrite protection, EOF
  diagnostics, safe cleanup, and saved output decoding/count checks.
- `timestamps.py`: decoder-first, clip-relative monotonic times with FPS fallback
  provenance; the inference stride does not change source time or saved frame count.
- `preview.py`: isolated GUI backend probe and optional project-local Linux GUI libs.
- `annotation.py`: fresh frame boxes/time; refers users to final temporal result,
  without claiming live decision overlays (reserved for Phase 4).
- `decision.py`: pure-Python timestamp-based candidate state machine, rolling
  evidence rules, confirmation, rejection, and partial-candidate diagnostics.
- `fingerprint.py`: streaming source identity/mutation checks.
- `annotation.py`: configurable live decision-state overlay with explicit pending/
  confirmed language and model-score separation.
- `evidence.py`: bounded before/strongest/after source-frame selection with a
  sequential second decode; no repeat inference or continuous evidence saving.
- `contracts.py`: semantic v1.0 result validation, stable incident identity, and
  registered-metadata constraints.
- `atomic.py`, `reporting.py`, `summaries.py`: fsync/atomic writes, complete
  report/evidence bundles, Markdown report, and timing/uncertainty summaries.
- `retention.py`: opt-in cleanup of verified managed bundles only; user-modified,
  malformed, symlinked, unmanaged, current, and protected paths are preserved.
- `integration.py`: post-publication `IncidentCallback`/`IncidentEvent` seam and
  local JSONL simulation sink; no network or emergency action.
- `evaluate.py`: strict whole-video manifest parser, calibration/test split policy,
  video/incident metrics, delays, missed events, and false-confirmed-per-hour rate.
- `benchmark.py`: local speed measurements with explicit model/resource settings;
  benchmark results are not accuracy claims.
- `workflow.py`: orchestrates components and returns observations plus decisions;
  always cleans resources and keeps partial/complete processing distinct.
- `tests/`: offline standard-library unittest suite; temporary fixtures are
  created beneath `outputs/` and cleaned automatically.

## Storage boundaries

| Directory | Purpose |
| --- | --- |
| `.venv/` | Project-local interpreter and installed dependencies |
| `models/` | Explicitly supplied checkpoints and existing export metadata |
| `test_clip/` | Existing sample footage and future local test videos |
| `outputs/` | Basic annotated videos and redirected observation/temporal JSON |
| `incidents/` | Versioned JSON/Markdown report bundles and selected evidence images |
| `logs/` | Runtime diagnostics and dependency settings/caches |

All videos, weights, credentials, caches, and generated artifacts are ignored.
Source documentation `REPORT.md` is retained. Nothing trains or downloads weights.

## Dependency plan

Use the standard library for config, CLI, hashing, paths, and ordinary tests.
Use Ultralytics/PyTorch/torchvision for the supplied detector and OpenCV/NumPy for
local decoding and annotation. `requirements.txt` constrains compatible
release families; Ultralytics also installs its own transitive dependencies.

The current WSL environment uses CPU PyTorch 2.11.0 and torchvision 0.26.0,
Ultralytics 8.4.174, OpenCV 4.14.0, and NumPy 2.5.2 on Python 3.14.4.
CPU wheels were explicitly installed to establish model-loading capability without
a large CUDA dependency installation. An RTX 4050 is visible to `nvidia-smi`, but
CUDA is **not available to this installed CPU PyTorch build**. GPU execution will
require compatible CUDA wheels and its own verification.

## Video and observation contract (Phases 2–3)

Configuration has backward-compatible optional `inference`/`video` blocks with
strict validation. Explicit CLI values override defaults. The reader decodes all
frames; the adapter samples frame 0 and every Nth frame. Save-mode retains all
decoded frames at original size and source/assumed FPS. Decoder times are preferred;
FPS estimates and output constant-FPS limitations are recorded honestly.

Positive observations record frame index, clip time/provenance, and typed
detections. Phase 3 separately ingests **all inferred frames**, including negatives,
and advances video time for skipped frames without giving them votes. Candidate
grouping/confirmation uses actual verified accident classes, not a hard-coded ID.
Phase 2 archived JSON retains null/unassessed results; current Phase 3 results
include confirmed/rejected/unresolved candidates and coverage-sensitive decisions.
Neither provisional format claims the stable Phase 4 incident contract.

Optional Linux GUI dependencies (`libsm6`/`libice6`) are installed locally beneath
`.venv/lib/native/`; no system change or runtime library download is performed.
The preview checks the backend in a child interpreter before opening its window.

## Temporal decision boundary (Phase 3)

`TemporalDecisionEngine` accepts a verified class-name mapping, `DecisionConfig`,
and typed detection observations with source frame indices/video timestamps. It
imports no detector, OpenCV, NumPy, or PyTorch. Detector confidence filtering is
separate from temporal supporting-score thresholds and evidence windows.

The state machine follows NORMAL → POSSIBLE → VERIFYING → CONFIRMED, or rejects
an unconfirmed completed candidate as FALSE_ALARM. Confirmed candidates latch
until the grouping timeout or processing end; closing returns the engine to NORMAL
without retroactively declaring the physical scene safe. Partial pending candidates
stay unresolved. Each candidate records support time range, strongest frame,
score evidence, counts/continuity, confirmation metrics, and state/closure reasons.

See [DECISIONS.md](DECISIONS.md) for exact rules and limitations. The new module
adds no dependencies; thresholds are illustrative, uncalibrated starting points.

## Phase 4 artifact boundary

The Phase 4 result is schema **1.0**, published with a stable media/model/settings
identity and a unique run ID. An atomic same-filesystem rename publishes a complete
bundle only after the report, evidence, semantic validation, and `COMPLETE.json`
inventory pass. Evidence contains no overlays and is limited to three selected
source frames per confirmed candidate. The live overlay is intentionally distinct
from the JSON/report contract: it is a display aid, not a new evidence source.
See [RESULTS.md](RESULTS.md) and [result.schema.json](result.schema.json).

`--no-save` disables report/evidence/video artifacts while still returning an
in-memory versioned result. `--no-evidence` keeps reports without images. Retention
is disabled by default and only operates on verified managed bundles when enabled.
Registered camera/location values remain explicitly user-supplied labels.

## Phase 5 integration boundary

The optional callback is invoked only after a complete local result bundle has
published successfully. It receives one immutable event per confirmed candidate;
rejected/unresolved candidates do not emit events. The event contains stable IDs,
source/checkpoint hashes, temporal support/confirmation times, score evidence,
relative evidence references, and explicitly registered metadata. It sets human
review required and emergency action unauthorized. Callback failure is surfaced
after local publication, so reporting integrity is not rewritten by notification.

`SimulationAlertSink` appends local JSONL with `simulation: true`; it does not
import/use network clients, sockets, SMS, phone, dispatch, credentials, or cloud
services. Future FastAPI/mobile/emergency adapters must be separate from the
detector, decision engine, video I/O, and reporting modules and must require human
verification before any emergency action. See [INTEGRATION.md](INTEGRATION.md).

## Phase 6 handoff boundary

Evaluation uses complete local videos from a version 1 manifest. Each video appears
once and belongs to calibration or test; labels/events are clip-level, not frame-level.
The evaluator warns about camera/location overlap, keeps partial runs inconclusive,
and never tunes thresholds. `evaluate.py --validate-only` validates a template without
weights; ordinary evaluation reports measured values only for supplied labels.
`benchmark.py` records local throughput/resource settings separately from accuracy.

No labeled dataset is currently present, so no real precision/recall/reliability
claim is made. Future handoff should retain provenance, whole-video split policy,
hard negatives, source/checkpoint hashes, calibration settings, and actual local
benchmark conditions.

## Future phase boundaries — design only

No further implementation phase is planned in the supplied six-phase scope.

## Validation and uncertainty

The supplied model's actual mapping is `{0: 'Accident'}` and its task is `detect`.
It detects accident-like image regions; one prediction cannot confirm a video
incident. Model confidence will remain distinct from temporal evidence and final
decision. Precise vehicle motion, injuries, or scene location cannot be inferred
from this class mapping.

Existing export metrics (precision 0.951111, recall 0.971402, mAP50 0.98774,
mAP50–95 0.857349) are upstream reported detection validation metrics, not locally
verified video-level performance. Two sample videos have no provided event labels
or provenance that establishes held-out status. Real-world accuracy is not yet
established. Future calibration and test sets must be separate by whole video
and preferably camera/location; thresholds must not be tuned on the final test set.
