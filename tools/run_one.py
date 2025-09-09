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
    from wotbot.launcher.login_flow import login_once, LoginInvalidError, GameStartTimeoutError
    import subprocess

    try:
        clf = PHashStateClassifier(dataset_root)
        total = clf.load()
        logger.info(f"Loaded {total} templates for scenes")
    except Exception as exc:
        logger.error(f"Classifier load failed: {exc}")
        sys.exit(1)

    creds = Credentials(email=args.email, password=args.password)
    # Повторы логина: до 3 попыток, если что-то пошло не так
    ok = False
    invalid_creds = False
    for attempt in range(1, 4):
        try:
            logger.info(f"Login attempt {attempt}/3")
            ok = login_once(dataset_root, creds)
        except LoginInvalidError as exc:
            logger.error(f"Invalid credentials: {exc}")
            invalid_creds = True
            ok = False
            break
        except GameStartTimeoutError as exc:
            logger.error(f"Game start timeout: {exc}")
            ok = False
            break
        except Exception as exc:
            logger.exception(f"Login flow exception (attempt {attempt}): {exc}")
            ok = False
        if ok:
            break
        time.sleep(1.0)
    if not ok:
        # 3 = special code for invalid credentials (skip retries in run_all)
        sys.exit(3 if invalid_creds else 1)

    # Give the game a moment to spawn the window
    time.sleep(5.0)

    # Enforce hard cap of 5 minutes for game flow
    max_secs_capped = min(int(args.max_secs_game), 300)
    if max_secs_capped != int(args.max_secs_game):
        logger.info(f"Cap max-secs-game to 300s (was {args.max_secs_game})")

    cmd = [
        sys.executable,
        "-m", "tools.run_game_flow",
        "--dataset", str(dataset_root),
        "--templates", str(templates_dir),
        "--max-secs", str(max_secs_capped),
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


