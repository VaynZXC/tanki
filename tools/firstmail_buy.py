from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wotbot.logging_setup import setup_logging
from wotbot.registration.firstmail_http import FirstmailHttpClient


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Buy mailbox from Firstmail Market API")
    ap.add_argument("--type", type=int, default=3, help="Mailbox type (e.g. 3 for permanent)")
    ap.add_argument("--count", type=int, default=1, help="How many mailboxes to buy (loop)")
    ap.add_argument("--all-available", action="store_true", help="Drain all available mails for this API key")
    ap.add_argument("--append", type=str, default="accounts.txt", help="Append to file as email\tpassword")
    ap.add_argument("--base-url", type=str, default="https://api.firstmail.ltd/v1", help="Base URL for API (v1.4.1)")
    ap.add_argument("--path", action="append", default=["/market/buy/mail", "/lk/get/email"], help="Endpoint path(s) to try (can repeat)")
    ap.add_argument("--auth", action="append", default=["x-api-key"], help="Auth modes to try")
    ap.add_argument("--method", action="append", default=["POST", "GET"], help="HTTP methods to try")
    ap.add_argument("--payload", action="append", default=["params", "json", "data"], help="Payload kind: params|json|data")
    ap.add_argument("--api-key", type=str, default=None, help="Override API key value")
    ap.add_argument("--key-file", type=str, default=None, help="Path to file with API key (first line)")
    ap.add_argument("--workers", type=int, default=8, help="Parallel workers for buy requests")
    return ap.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    client = FirstmailHttpClient(api_key=args.api_key, base_url=args.base_url, key_file=args.key_file)
    out_path = Path(args.append)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bought = 0
    seen: set[str] = set()

    def write_entry(email: str, password: str, left_val: object | None) -> None:
        nonlocal bought
        if left_val is not None:
            logger.info(f"Куплена почта: {email} | осталось в паке: {left_val}")
        else:
            logger.info(f"Куплена почта: {email}")
        with out_path.open("a", encoding="utf-8") as f:
            f.write(f"{email}\t{password}\n")
        bought += 1

    if args.all_available:
        # Параллельная выкачка всех доступных логинов
        stop_event = threading.Event()
        lock = threading.Lock()

        def worker_loop() -> int:
            local = 0
            while not stop_event.is_set():
                try:
                    mb = client.buy_mailbox(mailbox_type=args.type, paths=args.path, auth_modes=args.auth, methods=args.method, payloads=args.payload)
                except Exception as exc:
                    logger.warning(f"Worker error: {exc}")
                    break
                with lock:
                    if mb.email in seen:
                        # Дубликат — возможно пул пуст
                        stop_event.set()
                        break
                    seen.add(mb.email)
                    left = mb.raw.get("left") if isinstance(mb.raw, dict) else None
                    write_entry(mb.email, mb.password, left)
                    try:
                        left_int = int(left) if left is not None else None
                    except Exception:
                        left_int = None
                    if left_int is not None and left_int <= 0:
                        stop_event.set()
                local += 1
            return local

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futures = [ex.submit(worker_loop) for _ in range(max(1, args.workers))]
            for _ in as_completed(futures):
                pass
        logger.info(f"Итог: куплено {bought} (все доступные), сохранено в {out_path}")
        return

    # Режим фиксированного количества
    for _ in range(args.count):
        mb = client.buy_mailbox(mailbox_type=args.type, paths=args.path, auth_modes=args.auth, methods=args.method, payloads=args.payload)
        left = mb.raw.get("left") if isinstance(mb.raw, dict) else None
        write_entry(mb.email, mb.password, left)
    logger.info(f"Итог: куплено {bought}/{args.count}, сохранено в {out_path}")


if __name__ == "__main__":
    main()


