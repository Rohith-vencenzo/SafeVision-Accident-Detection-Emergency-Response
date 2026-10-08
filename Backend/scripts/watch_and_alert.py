"""Run the detector on a local clip and push any confirmed incident to the phone.

This is the operator entry point for the demonstration flow:

    Backend running  ->  this script analyses a clip  ->  incident created
    ->  FCM push  ->  alarm on the handset

The detector project is NOT imported or modified. ``DetectorAdapter`` invokes
``main.py`` exactly as the upload pipeline does, reusing its result validation,
source/checkpoint hash checks, and evidence bundle integrity checks.

A confirmed incident is temporal model evidence requiring human review. Nothing
here dispatches emergency services or calls anyone.
"""

import argparse
import time
from pathlib import Path

from app.config import get_settings
from app.db import get_session_factory
from app.services.detector import DetectorAdapter, DetectorAdapterError
from app.services.ingest import IngestError, ingest_detector_run, resolve_alert_owner
from scripts.common import run_operator


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a local clip and alert the registered handset")
    parser.add_argument("--source", required=True, help="Local video file to analyze")
    parser.add_argument("--login", default="", help="Alert recipient login id; defaults to DETECTOR_ALERT_LOGIN")
    parser.add_argument("--repeat", type=int, default=1, help="Number of times to analyze the clip (1 = single pass)")
    parser.add_argument("--interval-seconds", type=float, default=3.0, help="Pause between repeats")
    parser.add_argument("--no-notify", action="store_true", help="Create incidents but do not deliver notifications")
    parser.add_argument("--device", default="", help="Detector device, e.g. 0 for CUDA, or cpu. Empty follows config.json")
    parser.add_argument("--show", action="store_true", help="Open the detector preview window to watch the analysis")
    parser.add_argument("--live", action="store_true",
                        help="Alert the instant the temporal engine confirms, instead of waiting for the clip to finish")
    parser.add_argument("--port", type=int, default=8000, help="Backend port for --live webhook delivery")
    args = parser.parse_args()

    settings = get_settings()
    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"source video not found: {source}")

    if args.login:
        settings.detector_alert_login = args.login
    if not settings.detector_alert_login:
        raise SystemExit("set DETECTOR_ALERT_LOGIN in .env or pass --login")

    mode = "FCM" if settings.fcm_mode == "firebase" else "disabled (no push will reach the handset)"
    print(f"source        : {source}")
    print(f"alert recipient: {settings.detector_alert_login}")
    print(f"notifications : {mode}")
    print(f"device        : {args.device or 'from config.json'}{'  (preview window enabled)' if args.show else '  (headless)'}")
    if args.no_notify:
        print("notification delivery suppressed by --no-notify")

    extra_args: list[str] = []
    if args.live:
        token = settings.detector_alert_token
        if not token:
            raise SystemExit("--live requires DETECTOR_ALERT_TOKEN in .env, and the Backend must already be running")
        endpoint = f"http://127.0.0.1:{args.port}/api/v1/detector/alert"
        extra_args = ["--alert-webhook", endpoint, "--alert-token", token]
        print(f"live alerting : {endpoint}")
        print("                the end-of-run ingest is SKIPPED, so each incident alerts exactly once")

    for iteration in range(1, max(1, args.repeat) + 1):
        print(f"\n=== pass {iteration}/{max(1, args.repeat)} ===")
        # A private working directory per pass: each detector run publishes its
        # own bundle, and identical run ids must never overwrite a prior one.
        work_dir = Path(settings.private_data_dir).resolve() / "detector-runs" / str(int(time.time() * 1000))
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            run = DetectorAdapter().run(source, work_dir / "output", device=args.device or None,
                                        show_preview=args.show, extra_args=extra_args)
        except DetectorAdapterError as error:
            print(f"detector failed: {error}")
            continue

        print(f"decision      : {run.result['incident_decision']}")
        print(f"incidents     : {len(run.result['incidents'])} confirmed")
        if not run.result["incidents"]:
            print("no confirmed candidate; nothing to alert")
            continue
        if args.live:
            # Incidents were already delivered mid-run, so re-ingesting the
            # finished bundle here would alert the handset a second time.
            print("live mode    : end-of-run ingest skipped; the alarm was already raised")
            continue

        with get_session_factory()() as db:
            try:
                owner = resolve_alert_owner(db)
                created = ingest_detector_run(db, run.result, run.bundle_path, owner)
                # Read scalar ids while the session is still open; the ORM
                # instances are detached once the context manager exits.
                created_ids = [str(item.id) for item in created]
            except IngestError as error:
                print(f"ingest failed: {error}")
                continue

        for incident_id in created_ids:
            print(f"incident {incident_id}: queued for review")
        if created_ids and not args.no_notify:
            print("run 'python -m scripts.dispatch_notifications' to deliver, or leave the API running to dispatch automatically")

    print("\ndone. A confirmed alert still requires human review on the handset.")


if __name__ == "__main__":
    run_operator(main)