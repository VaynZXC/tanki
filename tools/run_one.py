from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from loguru import logger

from wotbot.launcher.login_flow import ensure_english_layout, Credentials


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run WoT for a single account: launcher login + in-game rewards")
    ap.add_argument("--dataset", type=str, default="dataset", help="Root dataset with scenes")
    ap.add_argument("--templates", type=str, default="dataset/templates", help="Templates directory")
    ap.add_argument("--email", type=str, required=True, help="Account email")
    ap.add_argument("--password", type=str, required=True, help="Account password")
    ap.add_argument("--max-secs-game", type=int, default=300, help="Max seconds for in-game flow")
    ap.add_argument("--vision-snapshots", action="store_true", help="Save periodic game snapshots")
    ap.add_argument("--vision-snap-interval", type=float, default=5.0, help="Seconds between snapshots")
    ap.add_argument("--result-file", type=str, default="", help="Path to write selected reward tank ids (comma-separated)")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    dataset_root = Path(args.dataset)
    templates_dir = Path(args.templates)
    if not dataset_root.exists():
        logger.error(f"Dataset not found: {dataset_root}")
        sys.exit(1)
    if not templates_dir.exists():
        logger.error(f"Templates not found: {templates_dir}")
        sys.exit(1)

    # Layout before any typing
    ensure_english_layout()

    # Import heavy modules after layout setup
    from wotbot.vision.state_classifier import PHashStateClassifier
    from wotbot.launcher.login_flow import login_once
    import subprocess

    try:
        clf = PHashStateClassifier(dataset_root)
        total = clf.load()
        logger.info(f"Loaded {total} templates for scenes")
    except Exception as exc:
        logger.error(f"Classifier load failed: {exc}")
        sys.exit(1)

    creds = Credentials(email=args.email, password=args.password)
    try:
        ok = login_once(dataset_root, creds)
    except Exception as exc:
        logger.exception(f"Login flow exception: {exc}")
        ok = False
    if not ok:
        sys.exit(1)

    # Give the game a moment to spawn the window
    time.sleep(5.0)

    cmd = [
        sys.executable,
        "-m", "tools.run_game_flow",
        "--dataset", str(dataset_root),
        "--templates", str(templates_dir),
        "--max-secs", str(args.max_secs_game),
    ]
    if args.result_file:
        cmd += ["--result-file", args.result_file]
    if args.vision_snapshots:
        cmd += ["--vision-snapshots", "--vision-snap-interval", str(args.vision_snap_interval)]
    rc = subprocess.run(cmd, capture_output=False).returncode
    # run_game_flow already returns 0 only when reaching game_ungar
    sys.exit(rc)


if __name__ == "__main__":
    main()


