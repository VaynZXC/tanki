from __future__ import annotations

from pathlib import Path
import sys
import argparse

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wotbot.logging_setup import setup_logging
from wotbot.registration.firstmail_http import FirstmailHttpClient


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Read last message (any) for first account in accounts.txt")
    ap.add_argument("--accounts", type=str, default="accounts.txt", help="Path to accounts file")
    ap.add_argument("--key-file", type=str, default=None, help="Path to API key file")
    ap.add_argument("--proxy", type=str, default="", help="Proxy for Firstmail API (host:port:user:pass)")
    return ap.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    p = Path(args.accounts)
    if not p.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
            logger.warning(f"Accounts not found: created empty {p}")
        except Exception:
            logger.error(f"Accounts not found and cannot create: {p}")
            return
    first_line = ""
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        first_line = line
        break
    if not first_line:
        logger.error("Accounts file is empty")
        return
    parts = first_line.split("\t") if "\t" in first_line else first_line.split()
    if len(parts) < 2:
        logger.error("First line does not contain email and password")
        return
    email, password = parts[0].strip(), parts[1].strip()

    client = FirstmailHttpClient(key_file=args.key_file, proxy_url=(args.proxy or None))
    data = client.get_last_message_any(email=email, username=email, password=password)
    logger.info("Fetched last message (any)")
    import json
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


