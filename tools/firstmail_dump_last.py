from __future__ import annotations

import argparse
from pathlib import Path
import sys

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wotbot.logging_setup import setup_logging
from wotbot.registration.firstmail_http import FirstmailHttpClient


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Dump last unread email text from Firstmail")
    ap.add_argument("--email", required=True, help="Mailbox email (username)")
    ap.add_argument("--password", required=True, help="Mailbox password")
    ap.add_argument("--api-key", type=str, default=None, help="Firstmail API key (overrides)")
    ap.add_argument("--key-file", type=str, default=None, help="Path to file with API key")
    ap.add_argument("--base-url", type=str, default="https://api.firstmail.ltd/v1", help="API base URL")
    ap.add_argument("--proxy", type=str, default="", help="Proxy for Firstmail API (host:port:user:pass)")
    ap.add_argument("--unread-only", action="store_true", help="Only fetch unread via Market API (no IMAP fallback)")
    ap.add_argument("--raw", action="store_true", help="Print raw JSON as well")
    return ap.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    client = FirstmailHttpClient(api_key=args.api_key, base_url=args.base_url, key_file=args.key_file, proxy_url=(args.proxy or None))
    # 1) Market API (unread) — сразу с username/password (требуется для купленных ящиков)
    used = "market"
    try:
        if args.unread-only:
            data = client.get_last_unread_message(args.email, username=args.email, password=args.password)
        else:
            data = client.get_last_message_any(args.email, username=args.email, password=args.password)
            used = "any"
    except Exception as exc:
        logger.warning(f"Fetch failed: {exc}")
        data = {}

    def _pick(obj: dict, keys: list[str]) -> str:
        for k in keys:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return ""

    subject = _pick(data, ["subject", "Subject", "subj"]) or ""
    from_addr = _pick(data, ["from", "From", "sender"]) or ""
    text = _pick(data, ["text", "body_text", "plain", "message", "content"]) or ""
    html = _pick(data, ["html", "body_html", "html_body", "bodyHtml"]) or ""

    # 2) Fallback: if Market returned empty content, force IMAP (unless unread-only)
    if used == "market" and not any([subject, from_addr, text, html]) and not args.unread-only:
        logger.warning("Market API returned empty content; retrying via IMAP")
        data = client.get_last_message_imap(args.email, username=args.email, password=args.password)
        subject = _pick(data, ["subject", "Subject", "subj"]) or ""
        from_addr = _pick(data, ["from", "From", "sender"]) or ""
        text = _pick(data, ["text", "body_text", "plain", "message", "content"]) or ""
        html = _pick(data, ["html", "body_html", "html_body", "bodyHtml"]) or ""

    logger.info(f"Subject: {subject}")
    logger.info(f"From: {from_addr}")
    print("\n===== TEXT =====\n")
    print(text or "<no text part>")
    print("\n===== HTML =====\n")
    print(html or "<no html part>")
    if args.raw or not any([subject, from_addr, text, html]):
        import json
        print("\n===== RAW JSON =====\n")
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


