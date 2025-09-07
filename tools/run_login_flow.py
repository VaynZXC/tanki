from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

from wotbot.launcher.login_flow import run_for_all_accounts, ensure_english_layout


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run WoT launcher login flow for all accounts")
    ap.add_argument("--dataset", type=str, default="dataset", help="Root of templates: dataset/<state>")
    ap.add_argument("--accounts", type=str, default="accounts.txt", help="Path to accounts file")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    dataset_root = Path(args.dataset)
    accounts_path = Path(args.accounts)
    if not dataset_root.exists():
        logger.error(f"Dataset not found: {dataset_root}")
        return
    if not accounts_path.exists():
        try:
            accounts_path.parent.mkdir(parents=True, exist_ok=True)
            accounts_path.touch()
            logger.warning(f"Accounts file not found: created empty {accounts_path}")
        except Exception:
            logger.error(f"Accounts file not found and cannot create: {accounts_path}")
            return

    # Ensure EN layout BEFORE any typing
    ensure_english_layout()

    run_for_all_accounts(dataset_root, accounts_path)


if __name__ == "__main__":
    main()
