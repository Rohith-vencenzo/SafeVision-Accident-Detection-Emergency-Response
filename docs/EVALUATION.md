# Evaluation and benchmarking — Phase 6

No labeled whole-video dataset was supplied with this project. Therefore no
accuracy result is invented for the two sample clips. Their filenames are not
labels. `evaluation_manifest.example.json` is only a format template.

## Manifest format

Copy the template and replace every placeholder with complete local videos:

```json
{
  "schema_version": 1,
  "videos": [
    {
      "id": "unique_clip_id",
      "path": "test_clip/clip.mp4",
      "label": "accident",
      "split": "calibration",
      "camera_id": "camera_a",
      "location_id": "site_a",
      "events": [{"start_seconds": 12.5, "end_seconds": 16.0}]
    },
    {
      "id": "unique_normal_id",
      "path": "test_clip/normal.mp4",
      "label": "normal",
      "split": "test",
      "camera_id": "camera_b",
      "location_id": "site_b",
      "events": []
    }
  ]
}
```

Required fields:

- `id`: unique whole-video identity;
- `path`: local complete video path, quoted on the shell when needed;
- `label`: `accident` or `normal`;
- `split`: `calibration` or `test`;
- `camera_id`/`location_id`: registered grouping metadata or `null`;
- `events`: labeled event intervals for accident videos, empty for normal videos.

The parser rejects duplicate IDs, duplicate paths, accident videos without event
intervals, normal events, invalid intervals, missing required keys, and unknown
splits. It validates complete video entries, not frame rows. A video cannot appear
in both splits. Neighboring frames must never be represented as independent
manifest entries.

## Split policy

Use calibration videos to choose detector/temporal thresholds. Hold the final test
videos out until settings are frozen. Split by whole video and, when possible, by
camera/location or source collection. The evaluator warns when a camera/location
group occurs in both calibration and test; that warning means measured metrics may
be correlated. It does not claim to fix leakage.

Include normal negatives and hard negatives when available: hard braking, close
following, intersecting traffic, parked/damaged vehicles, occlusion, shadows,
night/glare, compression, and camera movement. Do not use synthetic fixtures as
real accuracy evidence. Keep provenance notes for each clip and avoid selecting
thresholds on the final test set.

## Validate a manifest without model inference

The checked-in template contains placeholder files, so use `--validate-only`:

```bash
python evaluate.py --manifest "evaluation_manifest.example.json" --validate-only --split all --json
```

This validates schema/splits/whole-video identity without reading placeholder files.
After replacing it with real footage, omit `--validate-only`.

## Evaluate labeled videos

```bash
python evaluate.py \
  --manifest "evaluation_manifest.json" \
  --split test \
  --model "models/yolov11.pt" \
  --device cpu \
  --frame-stride 1 \
  --imgsz 640 \
  --json > "outputs/evaluation test.json"
```

Diagnostics/progress go to stderr; result JSON goes to stdout. Evaluation disables
report/video/evidence publication so it does not alter incident bundles.
`--max-frames` is available for smoke tests, but partial runs are inconclusive and
excluded from confusion counts. Use complete clips for metrics.

Reported metrics include:

- video-level TP/FP/FN/TN, precision, recall, F1, and inconclusive partial count;
- incident-level one-to-one interval matches, precision, recall, F1, missed events,
  false-confirmed events, and mean/median confirmation delay;
- false confirmed incidents per normal-video hour;
- per-video predictions, intervals, matches, missed incidents, and delays.

Incident matching is greedy one-to-one by largest interval overlap, with default
timestamp tolerance 0.5 seconds (`--tolerance-seconds`). Detection delay is
`confirmed_at_seconds - ground_truth.start_seconds`; observed negative/positive
values are not silently clipped. Ground-truth event times are video timestamps.

Video-level positive means at least one confirmed incident. Full clips with no
confirmation are scored negative for video confusion counts; partial runs are
inconclusive. Incident metrics count unmatched confirmations as false positives
and unmatched labeled events as misses. Normal confirmations contribute to the
false-confirmed-per-hour rate.

## Benchmark local speed

```bash
python benchmark.py \
  --source "test_clip/accident_demo.mp4" \
  --max-frames 120 \
  --imgsz 320 \
  --frame-stride 3 \
  --device cpu \
  --cpu-threads 4 \
  --json > "outputs/benchmark accident demo.json"
```

The benchmark records machine/Python, settings, decoded/inferred frames, wall
decoded/inferred FPS, inference-only FPS, duration, real-time factor, and timings.
It is a local diagnostic affected by filesystem, model warm-up, CPU/GPU load, and
chosen stride/resize. It is not an accuracy or reliability benchmark. Do not change
accuracy thresholds merely to improve benchmark appearance.

Weak-resource controls are `--frame-stride`, `--imgsz`, `--max-frames`,
`--cpu-threads`, and explicit `--device`. Unavailable acceleration fails explicitly.

## Handoff limitations

Until a real labeled manifest is supplied, the project has tested implementation
behavior, not unseen-video accuracy. Keep calibration/test split, camera/location
grouping, source/checkpoint hashes, and provenance with future measurements.
