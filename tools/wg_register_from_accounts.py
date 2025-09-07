from __future__ import annotations

import argparse
from pathlib import Path
import random
import string
import itertools
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wotbot.logging_setup import setup_logging
from wotbot.registration.wg_registration import register_on_site, confirm_via_firstmail, register_on_site_and_confirm_in_page
from wotbot.registration.firstmail_http import FirstmailHttpClient
from wotbot.registration.utils import generate_password


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Register WG accounts from accounts.txt using Playwright")
    ap.add_argument(
        "--site",
        type=str,
        default="https://join.worldoftanks.eu/1613051096/",
        help="Registration page URL (default: https://join.worldoftanks.eu/1613051096/)",
    )
    ap.add_argument("--accounts", type=str, default="accounts.txt", help="Path to accounts file email\tpassword (output)")
    ap.add_argument("--mails", type=str, default="mails.txt", help="Path to mails file (one email per line)")
    ap.add_argument("--headless", action="store_true", help="Run headless browser (UI hidden)")
    ap.add_argument("--limit", type=int, default=0, help="Limit number of accounts to process (0 = all)")
    ap.add_argument("--ref", type=str, default="EPICWIN", help="Referral code (default: EPICWIN)")
    ap.add_argument("--confirm", action="store_true", help="Fetch email from Firstmail and confirm via link (new tab)")
    ap.add_argument("--confirm-in-page", action="store_true", help="Confirm using the same browser page (poll Firstmail and open link in-page)")
    ap.add_argument("--confirm-wait", type=int, default=300, help="Wait seconds for confirmation email (polling)")
    ap.add_argument("--mailbox-pass", action="store_true", help="Use the same password from accounts.txt as mailbox password for Firstmail API")
    ap.add_argument("--firstmail-proxy", type=str, default="", help="Proxy for Firstmail API (host:port:user:pass)")
    ap.add_argument("--confirm-once", action="store_true", help="Single unread check (no polling)")
    ap.add_argument("--proxy", type=str, default="", help="Proxy in host:port:user:pass format for registration (Playwright). Empty = disabled")
    ap.add_argument("--proxy-file", type=str, default="proxy.txt", help="Path to file with proxies (one per line: host:port:user:pass). If exists, used round-robin per worker")
    ap.add_argument("--workers", type=int, default=1, help="Parallel workers for registration (default: 1)")
    ap.add_argument("--autobuy", action="store_true", help="Auto-buy Firstmail mailboxes if mails.txt is missing/empty (default: ON)")
    ap.add_argument("--target-total", type=int, default=100, help="How many accounts to create in total (defaults to 100). Counts existing in accounts file and stops when reached.")
    ap.add_argument("--autobuy-count", type=int, default=0, help="How many mailboxes to buy if empty (default: limit or workers)")
    ap.add_argument("--mail-type", type=int, default=3, help="Firstmail mailbox type (default: 3)")
    args = ap.parse_args()
    # enable autobuy by default if not explicitly disabled (flag style)
    # argparse with action='store_true' gives False when not provided; we want default True
    if '--autobuy' not in sys.argv:
        args.autobuy = True
    return args


def iter_accounts(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t") if "\t" in line else line.split()
        if len(parts) < 2:
            continue
        yield parts[0].strip(), parts[1].strip()


def iter_mails(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if "\t" in raw:
            parts = raw.split("\t")
        elif ":" in raw:
            parts = raw.split(":")
        else:
            parts = raw.split()
        email = parts[0].strip() if parts else ""
        mailbox_password = parts[1].strip() if len(parts) > 1 else ""
        if not email:
            continue
        yield email, (mailbox_password or None), raw


def main() -> None:
    setup_logging()
    args = parse_args()
    src = Path(args.accounts)
    mails_path = Path(args.mails)
    proxy_file = Path(args.proxy_file) if args.proxy_file else None

    processed = 0

    # Respect target_total: count already existing accounts and stop when reached
    try:
        existing_total = 0
        if src.exists():
            existing_total = sum(1 for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip())
        # how many more to do in this run
        remaining_target = max(0, int(args.target_total) - existing_total)
        if args.limit <= 0:
            # if user did not set --limit, derive limit from remaining_target
            args.limit = remaining_target if remaining_target > 0 else 0
        logger.info(f"Target total={args.target_total}, already have={existing_total}, remaining limit={args.limit if args.limit>0 else 'unlimited'}")
    except Exception:
        pass

    def build_proxy_arg(raw: str | None):
        if not raw:
            return None
        try:
            host, port, user, pwd = raw.split(":", 3)
            server = f"http://{host}:{port}"
            return {"server": server, "username": user, "password": pwd}
        except ValueError:
            return None

    def load_proxies(path: Path | None) -> list[str]:
        if not path or not path.exists():
            return []
        lines = []
        for ln in path.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            lines.append(s)
        return lines

    # Загружаем список прокси (если proxy.txt существует — используем его, иначе берём --proxy)
    proxies_raw: list[str] = load_proxies(proxy_file)
    if not proxies_raw and args.proxy:
        proxies_raw = [args.proxy]
    proxy_cycle = itertools.cycle(proxies_raw) if proxies_raw else None
    proxy_lock = threading.Lock()
    thread_proxy_map: dict[int, str] = {}

    def get_thread_proxy_raw() -> str | None:
        if not proxy_cycle:
            return None
        tid = threading.get_ident()
        with proxy_lock:
            raw = thread_proxy_map.get(tid)
            if raw is None:
                raw = next(proxy_cycle)
                thread_proxy_map[tid] = raw
            return raw

    # Если есть mails.txt — регистрируем по списку почт и пишем пары в accounts.txt
    def ensure_mails_available() -> None:
        nonlocal mails_path
        if not args.autobuy:
            return
        exists = mails_path.exists()
        existing_lines = []
        if exists:
            try:
                existing_lines = [ln.strip() for ln in mails_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            except Exception:
                existing_lines = []
        if exists and existing_lines:
            return
        # Нужно докупить
        count_needed = args.autobuy_count or (args.limit if args.limit > 0 else max(1, args.workers))
        logger.info(f"[autobuy] Buying {count_needed} mailbox(es) from Firstmail...")
        client = FirstmailHttpClient(proxy_url=(args.firstmail_proxy or None))
        bought: list[tuple[str, str]] = []
        for i in range(count_needed):
            try:
                mb = client.buy_mailbox(mailbox_type=args.mail_type)
                bought.append((mb.email, mb.password))
                logger.info(f"[autobuy] {i+1}/{count_needed}: {mb.email}")
            except Exception as exc:
                logger.warning(f"[autobuy] Buy failed ({i+1}/{count_needed}): {exc}")
        if not bought:
            logger.error("[autobuy] No mailboxes bought. Proceeding without mails.txt")
            return
        try:
            mails_path.parent.mkdir(parents=True, exist_ok=True)
            with mails_path.open("a", encoding="utf-8") as f:
                for email, pwd in bought:
                    f.write(f"{email}\t{pwd}\n")
            logger.info(f"[autobuy] Added {len(bought)} mailboxes to {mails_path}")
        except Exception as exc:
            logger.warning(f"[autobuy] Failed to write mails.txt: {exc}")

    ensure_mails_available()

    # Включаем подтверждение по умолчанию, если работаем с mails.txt и флаги не заданы
    do_in_page = bool(args.confirm_in_page)
    do_confirm = bool(args.confirm or args.confirm_in_page)
    if mails_path.exists() and not do_confirm:
        do_confirm = True
        logger.info("[confirm] Enabled by default (mails.txt detected, no flags provided)")
    # По умолчанию подтверждаем в той же вкладке, если пользователь не указывал флаги
    if do_confirm and not do_in_page and ("--confirm-in-page" not in sys.argv) and ("--confirm" not in sys.argv):
        do_in_page = True
        logger.info("[confirm] In-page mode enabled by default (same browser/page)")

    if mails_path.exists():
        out_path = src
        out_path.parent.mkdir(parents=True, exist_ok=True)
        original_lines = mails_path.read_text(encoding="utf-8").splitlines()
        consumed_raw: list[str] = []
        items = list(iter_mails(mails_path))
        if args.limit > 0:
            items = items[:args.limit]

        lock_out = threading.Lock()
        lock_count = threading.Lock()

        def worker(item: tuple[str, str | None, str]) -> None:
            nonlocal processed
            email, mailbox_pwd, raw_line = item
            if not mailbox_pwd:
                logger.warning(f"No mailbox password for {email} in mails file — skipping")
                return
            password = mailbox_pwd
            local_part = email.split("@", 1)[0]
            suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(6))
            user_name = f"{local_part}{suffix}2025"
            # Проксирование: каждому потоку — свой прокси
            proxy_raw = get_thread_proxy_raw()
            pw_proxy = build_proxy_arg(proxy_raw) if proxy_raw else None
            # Firstmail API по умолчанию без прокси; используем только если явно задан --firstmail-proxy
            fm_proxy_raw = args.firstmail_proxy or None
            try:
                final_ok = False
                if do_in_page:
                    res = register_on_site_and_confirm_in_page(
                        email=email,
                        password=password,
                        name=user_name,
                        url=args.site,
                        referral_code=args.ref,
                        headless=args.headless,
                        mailbox_password=password,
                        confirm_timeout_sec=args.confirm_wait,
                        proxy=pw_proxy,
                        firstmail_proxy=fm_proxy_raw,
                        confirm_once=args.confirm_once,
                    )
                    final_ok = res.ok
                else:
                    res = register_on_site(
                        email=email,
                        password=password,
                        name=user_name,
                        url=args.site,
                        referral_code=args.ref,
                        headless=args.headless,
                        proxy=pw_proxy,
                    )
                    if not res.ok:
                        logger.warning(f"FAIL: {email} | {res.error}")
                    else:
                        if do_confirm:
                            ok = confirm_via_firstmail(email, timeout_sec=args.confirm_wait, headless=args.headless, mailbox_password=password, firstmail_proxy=fm_proxy_raw)
                            logger.info(f"Confirm {'OK' if ok else 'FAIL'}: {email}")
                            final_ok = ok
                        else:
                            final_ok = True

                if final_ok:
                    logger.info(f"OK: {email}")
                    with lock_out:
                        with out_path.open("a", encoding="utf-8") as f:
                            f.write(f"{email}\t{password}\n")
                        consumed_raw.append(raw_line)
            except Exception as exc:
                logger.warning(f"Worker error for {email}: {exc}")
            finally:
                with lock_count:
                    processed += 1

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futures = [ex.submit(worker, it) for it in items]
            for _ in as_completed(futures):
                pass
        # Перезаписываем mails.txt, удаляя успешно зарегистрированные
        remaining = [ln for ln in original_lines if ln.strip() not in set(consumed_raw)]
        mails_path.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")
    else:
        if not src.exists():
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                src.touch()
                logger.warning(f"Accounts not found: created empty {src}")
            except Exception:
                logger.error(f"Accounts not found and cannot create: {src}")
                return
        items_acc = list(iter_accounts(src))
        if args.limit > 0:
            items_acc = items_acc[:args.limit]
        # Если регистрируем из accounts.txt и подтверждение не задано — не включаем по умолчанию
        do_in_page_acc = bool(args.confirm_in_page)
        do_confirm_acc = bool(args.confirm or args.confirm_in_page)
        # Если подтверждение включено, но флаги не заданы — по умолчанию в той же вкладке
        if do_confirm_acc and not do_in_page_acc and ("--confirm-in-page" not in sys.argv) and ("--confirm" not in sys.argv):
            do_in_page_acc = True
            logger.info("[confirm] In-page mode enabled by default (same browser/page)")
        lock_out = threading.Lock()
        lock_count = threading.Lock()

        def worker_acc(item: tuple[str, str]):
            nonlocal processed
            email, password = item
            proxy_raw = get_thread_proxy_raw()
            pw_proxy = build_proxy_arg(proxy_raw) if proxy_raw else None
            # Firstmail API по умолчанию без прокси; используем только если явно задан --firstmail-proxy
            fm_proxy_raw = args.firstmail_proxy or None
            try:
                if do_in_page_acc:
                    res = register_on_site_and_confirm_in_page(
                        email=email,
                        password=password,
                        url=args.site,
                        referral_code=args.ref,
                        headless=args.headless,
                        mailbox_password=(password if args.mailbox_pass else None),
                        confirm_timeout_sec=args.confirm_wait,
                        proxy=pw_proxy,
                        firstmail_proxy=fm_proxy_raw,
                        confirm_once=args.confirm_once,
                    )
                else:
                    res = register_on_site(
                        email=email,
                        password=password,
                        url=args.site,
                        referral_code=args.ref,
                        headless=args.headless,
                        proxy=pw_proxy,
                    )
                final_ok = False
                if res.ok:
                    if do_in_page_acc:
                        final_ok = res.ok
                    else:
                        if do_confirm_acc:
                            ok = confirm_via_firstmail(email, timeout_sec=args.confirm_wait, headless=args.headless, mailbox_password=(password if args.mailbox_pass else None), firstmail_proxy=fm_proxy_raw)
                            logger.info(f"Confirm {'OK' if ok else 'FAIL'}: {email}")
                            final_ok = ok
                        else:
                            final_ok = True
                else:
                    logger.warning(f"FAIL: {email} | {res.error}")
                if final_ok:
                    logger.info(f"OK: {email}")
            except Exception as exc:
                logger.warning(f"Worker error for {email}: {exc}")
            finally:
                with lock_count:
                    processed += 1

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futures = [ex.submit(worker_acc, it) for it in items_acc]
            for _ in as_completed(futures):
                pass

    logger.info(f"Готово. Обработано: {processed}")


if __name__ == "__main__":
    main()

