from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

# add project root to sys.path so 'wotbot' is importable when running as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wotbot.launcher.login_flow import ensure_english_layout
from wotbot.launcher.login_flow import _read_accounts as _read_accounts_login
from wotbot.launcher.login_flow import Credentials


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run full WoT pipeline: launcher login + in-game rewards")
    ap.add_argument("--dataset", type=str, default="dataset", help="Root dataset with scenes and templates")
    ap.add_argument("--templates", type=str, default="dataset/templates", help="Templates directory")
    ap.add_argument("--accounts", type=str, default="accounts.txt", help="Accounts file: email<TAB>password")
    ap.add_argument("--max-secs-game", type=int, default=300, help="Max seconds for in-game flow per account")
    ap.add_argument("--vision-snapshots", action="store_true", help="Save periodic game snapshots")
    ap.add_argument("--vision-snap-interval", type=float, default=5.0, help="Seconds between snapshots")
    return ap.parse_args()


def _run_game_flow(dataset: Path, templates: Path, max_secs: int, snaps: bool, snap_int: float) -> int:
    cmd = [
        sys.executable,
        "-m", "tools.run_game_flow",
        "--dataset", str(dataset),
        "--templates", str(templates),
        "--max-secs", str(max_secs),
    ]
    if snaps:
        cmd += ["--vision-snapshots", "--vision-snap-interval", str(snap_int)]
    logger.info(f"Запуск игрового потока: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=False)
    return proc.returncode


def main() -> None:
    args = parse_args()
    dataset_root = Path(args.dataset)
    templates_dir = Path(args.templates)
    accounts_path = Path(args.accounts)
    if not dataset_root.exists():
        logger.error(f"Dataset not found: {dataset_root}")
        return
    if not templates_dir.exists():
        logger.error(f"Templates not found: {templates_dir}")
        return
    if not accounts_path.exists():
        try:
            accounts_path.parent.mkdir(parents=True, exist_ok=True)
            accounts_path.touch()
            logger.warning(f"Accounts file not found: created empty {accounts_path}")
        except Exception:
            logger.error(f"Accounts file not found and cannot create: {accounts_path}")
            return

    ensure_english_layout()

    # Импортируем тут, чтобы избежать конфликтов ввода до переключения раскладки
    from wotbot.launcher.login_flow import login_once
    from wotbot.vision.state_classifier import PHashStateClassifier

    # Быстрая проверка датасета
    try:
        clf = PHashStateClassifier(dataset_root)
        total = clf.load()
        logger.info(f"Loaded {total} templates for scenes")
    except Exception as exc:
        logger.error(f"Classifier load failed: {exc}")
        return

    # Перезапуск логики на каждый аккаунт в отдельном процессе, чтобы сбросить состояние
    accounts_final = Path("accounts_final")
    accounts_final.mkdir(parents=True, exist_ok=True)
    ok_list_path = accounts_final / "ok.txt"

    def _remove_account_line(path: Path, email: str, password: str) -> None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return
        removed = False
        kept: list[str] = []
        for ln in lines:
            if removed:
                kept.append(ln)
                continue
            parts = (ln.split("\t") if "\t" in ln else ln.split())
            if len(parts) >= 2 and parts[0].strip() == email and parts[1].strip() == password:
                removed = True
                continue
            kept.append(ln)
        try:
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        except Exception:
            pass
    for creds in _read_accounts_login(accounts_path):
        logger.info(f"=== Аккаунт: {creds.email} ===")
        # Запускаем одноцикловый скрипт
        # dynamic result file to capture chosen tanks
        result_tmp = accounts_final / "_last_result.txt"
        cmd = [
            sys.executable, "-m", "tools.run_one",
            "--dataset", str(dataset_root),
            "--templates", str(templates_dir),
            "--email", creds.email,
            "--password", creds.password,
            "--max-secs-game", str(args.max_secs_game),
            "--result-file", str(result_tmp),
        ]
        if args.vision_snapshots:
            cmd += ["--vision-snapshots", "--vision-snap-interval", str(args.vision_snap_interval)]
        logger.info(f"Запуск отдельного процесса для {creds.email}")
        # Повторы полного цикла (логин+игра) на случай сбоя запуска игры
        # Особый код 3 = невалидные учётные данные, попытки не повторяем
        rc = 1
        for attempt in range(1, 3):
            logger.info(f"Run-one attempt {attempt}/2 for {creds.email}")
            rc = subprocess.run(cmd, capture_output=False).returncode
            if rc == 0:
                break
            if rc == 3:
                logger.warning(f"Invalid credentials for {creds.email} — skipping further attempts")
                break
            time.sleep(1.0)
        if rc == 0:
            logger.info(f"SUCCESS {creds.email}. Добавляю в accounts_final")
            # read result tanks and choose file name
            tanks = ""
            try:
                if result_tmp.exists():
                    tanks = result_tmp.read_text(encoding="utf-8").strip().replace(",", "_")
            except Exception:
                tanks = ""
            target_file = ok_list_path
            if tanks:
                safe_name = ''.join(ch if ch.isalnum() or ch in {'_', '-'} else '_' for ch in tanks)
                target_file = accounts_final / f"{safe_name}.txt"
            with target_file.open("a", encoding="utf-8") as f:
                f.write(f"{creds.email}\t{creds.password}\n")
            # удаляем использованный аккаунт из исходного списка
            _remove_account_line(accounts_path, creds.email, creds.password)
            # продолжаем к следующему аккаунту
            continue
        else:
            logger.warning(f"FAIL {creds.email} (rc={rc}). Записываю в 'ошибка_сбора.txt' и продолжаю")
            error_file = accounts_final / "ошибка_сбора.txt"
            try:
                with error_file.open("a", encoding="utf-8") as f:
                    f.write(f"{creds.email}\t{creds.password}\n")
            except Exception:
                pass
            _remove_account_line(accounts_path, creds.email, creds.password)
            # продолжаем к следующему аккаунту
            continue


if __name__ == "__main__":
    main()


