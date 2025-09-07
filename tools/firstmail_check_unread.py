from __future__ import annotations

import argparse
from pathlib import Path
import sys

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wotbot.logging_setup import setup_logging
from wotbot.registration.firstmail_http import FirstmailHttpClient


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Check if there is an unread message via Firstmail Market API")
    ap.add_argument("--username", required=True, help="Mailbox username (usually full email)")
    ap.add_argument("--password", required=True, help="Mailbox password")
    ap.add_argument("--key-file", type=str, default=None, help="Path to file with API key")
    ap.add_argument("--proxy", type=str, default="", help="Proxy for Firstmail API (host:port:user:pass)")
    return ap.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    client = FirstmailHttpClient(key_file=args.key_file, proxy_url=(args.proxy or None))
    data = client.get_last_unread_message(email=args.username, username=args.username, password=args.password)
    has_any = bool(data)
    logger.info(f"Unread present: {has_any}")
    if has_any:
        logger.info(str(data))


if __name__ == "__main__":
    main()


