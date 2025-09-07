from __future__ import annotations

import sys
import time

from loguru import logger

from wotbot.logging_setup import setup_logging
from wotbot.config import load_launcher_config
from wotbot.win.window_finder import find_launcher_hwnd_by_titles
from wotbot.win.window_actions import restore_and_focus_window
from wotbot.win.tray import double_click_tray_icon_by_hints


def ensure_launcher_visible() -> bool:
    cfg = load_launcher_config()

    hwnd = find_launcher_hwnd_by_titles(cfg.window_title_patterns)
    if hwnd:
        if restore_and_focus_window(hwnd):
            logger.info("Лаунчер найден и активирован")
            return True
        else:
            logger.info("Лаунчер найден, но не удалось активировать — попробую через трей")

    # Try tray fallback
    ok = double_click_tray_icon_by_hints(cfg.tray_icon_name_hints, cfg.max_tray_probe_icons)
    if ok:
        time.sleep(0.8)
        hwnd = find_launcher_hwnd_by_titles(cfg.window_title_patterns)
        if hwnd and restore_and_focus_window(hwnd):
            logger.info("Лаунчер развёрнут из трея и активирован")
            return True

    logger.warning("Не удалось обнаружить и развернуть лаунчер")
    return False


def main() -> int:
    setup_logging()
    logger.info("Проверяю наличие лаунчера и пытаюсь его развернуть…")
    ok = ensure_launcher_visible()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
