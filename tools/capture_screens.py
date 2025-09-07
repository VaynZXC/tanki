from __future__ import annotations

import os
import time
from pathlib import Path
from datetime import datetime
import argparse

import keyboard
import pyautogui
from loguru import logger

try:
    from wotbot.config import load_launcher_config, load_game_config
    from wotbot.win.window_finder import find_launcher_hwnd_by_titles, find_game_hwnd_by_titles
except ModuleNotFoundError:
    import sys
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from wotbot.config import load_launcher_config, load_game_config
    from wotbot.win.window_finder import find_launcher_hwnd_by_titles, find_game_hwnd_by_titles


DATASET_DIR_LAUNCHER = Path("dataset/raw")
DATASET_DIR_GAME = Path("dataset/game_raw")


def _screenshot_region(left: int, top: int, width: int, height: int, out_path: Path) -> None:
    img = pyautogui.screenshot(region=(left, top, width, height))
    img.save(out_path)


def _get_window_region(target: str) -> tuple[int, int, int, int] | None:
    try:
        import win32gui
        if target == "game":
            cfg = load_game_config()
            hwnd = find_game_hwnd_by_titles(cfg.window_title_patterns)
        else:
            cfg = load_launcher_config()
            hwnd = find_launcher_hwnd_by_titles(cfg.window_title_patterns)
        if not hwnd:
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return left, top, right - left, bottom - top
    except Exception:
        return None


def _capture_once(target: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_dir = DATASET_DIR_GAME if target == "game" else DATASET_DIR_LAUNCHER
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"shot_{ts}.png"

    region = _get_window_region(target)
    if region is None:
        logger.info("Окно не найдено — сохраню полноэкранный скрин")
        img = pyautogui.screenshot()
        img.save(out_path)
        return out_path

    left, top, width, height = region
    _screenshot_region(left, top, width, height, out_path)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Горячая клавиша для скринов лаунчера/игры")
    ap.add_argument("--target", choices=["launcher", "game"], default="game")
    ap.add_argument("--strict", action="store_true", help="Если окно не найдено, не делать полноэкранный скрин")
    args = ap.parse_args()

    logger.info("Горячая клавиша: Ctrl+S — сохранить скрин. Ctrl+C — выход.")
    def do_capture() -> None:
        region = _get_window_region(args.target)
        if region is None:
            if args.strict:
                logger.warning("Окно не найдено — strict режим, пропускаю снимок")
                return
            logger.info("Окно не найдено — сохраню полноэкранный скрин")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            out_dir = DATASET_DIR_GAME if args.target == "game" else DATASET_DIR_LAUNCHER
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"shot_{ts}.png"
            img = pyautogui.screenshot()
            img.save(out_path)
            logger.info(f"Сохранено: {out_path}")
            return
        l, t, w, h = region
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_dir = DATASET_DIR_GAME if args.target == "game" else DATASET_DIR_LAUNCHER
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"shot_{ts}.png"
        _screenshot_region(l, t, w, h, out_path)
        logger.info(f"Сохранено: {out_path}")

    keyboard.add_hotkey("ctrl+s", do_capture)

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        logger.info("Выход")


if __name__ == "__main__":
    main()
