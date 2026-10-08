# Temporal incident rules — Phase 3

**These rules confirm model evidence, not independent real-world truth.** They
suppress isolated spikes by construction, but their effect on unseen-video
precision/recall is unmeasured. Persistent wrong detections can still pass them.
Scores are not calibrated probabilities. Human review of the source/output is needed.

## Distinct layers

1. **Detector filtering:** `inference.confidence` / `--confidence` decides which
   boxes the detector returns (default 0.4).
2. **Supporting model evidence:** among actual verified accident classes, boxes
   with score at least `decision.support_confidence` (default 0.6) count as support.
   Ten boxes on a single frame still provide **one** supporting frame.
3. **Temporal verification:** the candidate's rolling inference observations must
   meet all persistence/ratio/continuity rules together.
4. **Video decision:** confirmed candidates and clip coverage determine the
   reported true/false/inconclusive value. This is not ground-truth classification.

The verified supplied checkpoint has task `detect` and mapping `{0: 'Accident'}`.
Its accident IDs come from the loaded metadata/config, not a constant in the engine.
Irrelevant classes are ignored. Model replacement requires confirmed class names;
the decision engine itself does not load/checkpoint weights or infer vehicle physics.

## Default rules and CLI overrides

Defaults were selected before Phase 3 sample runs, as illustrative starting points.
No labeled calibration or test set was provided; they were not tuned on sample outcomes.

| Config key (`decision`) | Default | CLI override | Meaning |
| --- | ---: | --- | --- |
| `support_confidence` | 0.6 | `--event-confidence` | Qualifying uncalibrated score threshold |
| `window_seconds` | 1.5 | `--event-window-seconds` | Rolling candidate video-time evidence window |
| `min_support_frames` | 3 | `--event-min-frames` | Supporting inferred frames in the same window |
| `min_support_span_seconds` | 0.5 | `--event-min-span-seconds` | First-to-last support span inside that window |
| `min_positive_ratio` | 0.6 | `--event-min-ratio` | Supporting frames / observed inferred frames in the window |
| `min_consecutive_frames` | 3 | `--event-min-consecutive` | Current trailing run of supporting inferred frames |
| `max_sample_gap_seconds` | 0.5 | `--event-max-sample-gap-seconds` | Larger inter-observation gaps reset the window/continuity |
| `event_gap_seconds` | 1.0 | `--event-gap-seconds` | Close a candidate once time since its last support exceeds this |

Every confirmation requires all rules simultaneously on a supporting frame:

- at least 3 support frames;
- first-to-last window support span at least 0.5 seconds;
- at least 60% qualifying support among inferred frames in the current window;
- at least 3 current consecutive supporting inferred frames;
- no inference gap greater than 0.5 seconds in that window.

The span is **not** a claim that a vehicle physically collided for that duration.
It may include short intervening misses, moderated by the ratio and current-run
requirements. Ratio is observation-count-based, not the fraction of wall/video
time occupied by an accident. The window starts with the candidate's first support.

Both minimum frame counts must be at least 2, and the span must be positive.
Configuration cannot enable single-frame confirmation. Window/span/gap settings
must be finite and internally consistent. Config without a `decision` block uses
these defaults; if a block is supplied, all its keys are required and validated.

Example diagnostic override (does not process a video):

```bash
python main.py --print-config --event-confidence 0.7 --event-min-frames 4
```

This demonstrates configuration, not a recommendation to use these values to
improve this demo. Changing settings requires evaluation on separate calibration videos.

## State machine and grouping

- **NORMAL:** no active candidate. Empty/irrelevant/weak frames never start one.
- **POSSIBLE:** the first qualifying supporting frame starts a candidate.
- **VERIFYING:** at least two consecutive supporting inference observations have
  occurred in its current window; the remaining criteria have not all passed.
  This stage can remain pending through short misses until timeout/end.
- **CONFIRMED:** all temporal rules passed together. Save the confirmation time
  and the exact window metrics that justified it. Confirmation is latched for the
  candidate; later misses do not retroactively undo its earlier qualifying evidence.
- **FALSE_ALARM:** a completed candidate never met all rules. Preserve it and
  diagnostic reasons. This term means **rejected by prototype rules**, not a
  verified real-world non-accident.

A support within the grouping gap remains in the same candidate. Once clip time
since the last qualifying support is **greater than** the grouping gap, close the
candidate and return the engine to NORMAL. A later support starts another candidate.
Grouping is by time only, not vehicle identity or collision geometry. Simultaneous
different collisions may merge; montage cuts/camera changes can merge or split
unrelated scenes. The system does not infer collisions from box overlap/proximity.

## Sampling and video time

Feed the engine one observation for every **inferred** frame, even when no box
was returned. Otherwise missing detections could artificially inflate persistence.
Skipped frames only advance the clip clock; they are neither positive nor negative.

Use the reader's decoder-based/estimated clip timestamps, not inference wall time
or the sampled-frame count. Multiple boxes on one frame are not extra votes. Large
sampling gaps reset the rolling window and consecutive evidence rather than
extrapolate unseen continuity. Excessive configured stride emits a warning when
its nominal cadence cannot satisfy the temporal window/gap/frame-count rules.

For example, stride 5 at 30 FPS produces an approximately 0.167-second cadence,
while stride 30 produces 1-second gaps that cannot meet the default 0.5-second
continuity limit. Increasing stride can miss events; lowering persistence can
increase false alarms. No thresholds are silently relaxed for a weak machine.

If detector confidence is set above temporal supporting confidence, the detector
may discard otherwise eligible support; a warning records this. The temporal layer
cannot recover filtered boxes. Model scores and temporal ratios are never combined
into an invented accident probability.

## End of clip, partial runs, and returned evidence

Flush the active candidate at EOF so a valid late-clip confirmation is retained.
A complete clip ending before sufficient support rejects the candidate with reasons.
A frame limit, corrupt early EOF, or preview stop keeps an unconfirmed pending
candidate in `unresolved_candidates`, preserving POSSIBLE/VERIFYING rather than
inventing a FALSE_ALARM. Already confirmed candidates remain present with partial
boundary flags; their existence does not establish coverage of the whole video.

| Coverage / evidence | `accident_detected` | `incident_decision` |
| --- | --- | --- |
| Any candidate passed the rules | `true` | `confirmed_incident` |
| Full processed clip, none passed | `false` | `no_confirmed_incident` |
| Partial clip, none passed | `null` | `inconclusive` |

Each confirmed/rejected/unresolved record retains:

- candidate number (local to this run, **not** a stable/global incident ID);
- first/last support timestamps, confirmation time, closure time/reason;
- support and observed-frame counts, weak/missing observations, lifetime longest
  continuous support run, maximum support gap, and score min/max/mean;
- strongest frame index/time/class/boxes/model score;
- supporting-frame metadata and score evidence (no image files yet);
- confirmation-window metrics and last-support-window metrics;
- state transitions, rejection reasons, and estimated-time/boundary flags.

`decision_state_history` includes engine transitions to NORMAL when candidates
close; NORMAL is an idle engine state, not a physical safety assessment. The result
also records counts of weak/irrelevant observations and sampling-window resets.

The current saved overlay shows only fresh frame boxes and says to consult the
final temporal result. Live decision overlays, selected before/at/after images,
stable IDs, retention, atomic incident writes, and the versioned contract belong
to the separately approved Phase 4.

## Checks and improving accuracy responsibly

The scripted suite verifies persistent positives, isolated spikes, separated
incidents, weak scores, normal observations, clip-end and partial candidates,
ratio/continuity failures, large sample gaps, multiple boxes, temporal boundaries,
and timestamp/class-map validation. Synthetic fixtures test the video/decision seam.
`tests.phase3_validation` also replays saved real detections with freshly decoded
timestamps/negative inference frames and compares all temporal evidence exactly.
These establish implementation behavior; **they do not establish accuracy**.

Future calibration must use labeled **whole videos**, separated from final test
videos and preferably grouped by camera/location. Never divide neighboring frames
between calibration and test, and never tune the final test set. Include labeled
normal footage and hard negatives (parked/damaged vehicles, hard braking, close
following, occlusion, night/glare/shadows, camera movement, and compression).
Measure incident/video precision, recall, delay, and false confirmations per hour
in Phase 6. Until that labeled evaluation exists, improved unseen-video performance
is a design goal, not a measured claim. No model training or substitution is automatic.
