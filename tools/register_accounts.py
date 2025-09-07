from __future__ import annotations

import argparse
from pathlib import Path
import sys

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wotbot.logging_setup import setup_logging
from wotbot.registration.firstmail_provider import FirstmailProvider
from wotbot.registration.wg_registration import register_single_account


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Register new WoT accounts using Firstmail")
    ap.add_argument("--count", type=int, default=1, help="Number of accounts to register")
    ap.add_argument("--output", type=str, default="accounts.txt", help="Where to append accounts (email\tpassword)")
    ap.add_argument("--domain", type=str, default="firstmail.ltd", help="Firstmail domain")
    ap.add_argument("--region", type=str, default="eu", help="WG region (eu/na/asia)")
    return ap.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    provider = FirstmailProvider(domain=args.domain)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    created = 0
    for _ in range(args.count):
        res = register_single_account(provider, region=args.region)
        if res.confirmed:
            with out_path.open("a", encoding="utf-8") as f:
                f.write(f"{res.email}\t{res.password}\n")
            logger.info(f"Создан и подтверждён аккаунт: {res.email}")
            created += 1
        else:
            logger.warning(f"Аккаунт не подтверждён: {res.email}")

    logger.info(f"Готово. Создано подтверждённых аккаунтов: {created}/{args.count}")


if __name__ == "__main__":
    main()


