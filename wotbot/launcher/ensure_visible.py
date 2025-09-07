from __future__ import annotations

import time
from loguru import logger

from wotbot.config import load_launcher_config
from wotbot.win.window_finder import find_launcher_hwnd_by_titles
from wotbot.win.window_actions import restore_and_focus_window
from pathlib import Path
from wotbot.win.tray import (
    double_click_tray_icon_by_hints,
    double_click_first_tray_icon,
    open_first_overflow_icon_keyboard,
    double_click_taskbar_icon_template,
)


def ensure_launcher_visible(timeout_sec: float = 3.0) -> bool:
    cfg = load_launcher_config()

    hwnd = find_launcher_hwnd_by_titles(cfg.window_title_patterns)
    if hwnd and restore_and_focus_window(hwnd):
        return True

    # Try dedicated template for pinned/exposed icon on taskbar (right side)
    wg_icon = Path("dataset/templates/wg_icon.png")
    if wg_icon.exists() and double_click_taskbar_icon_template(wg_icon, right_fraction=0.7):
        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            hwnd = find_launcher_hwnd_by_titles(cfg.window_title_patterns)
            if hwnd and restore_and_focus_window(hwnd):
                return True
            time.sleep(0.2)

    if (
        double_click_tray_icon_by_hints(cfg.tray_icon_name_hints, cfg.max_tray_probe_icons)
        or double_click_first_tray_icon()
        or open_first_overflow_icon_keyboard()
    ):
        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            hwnd = find_launcher_hwnd_by_titles(cfg.window_title_patterns)
            if hwnd and restore_and_focus_window(hwnd):
                return True
            time.sleep(0.2)

    logger.warning("ensure_launcher_visible: failed")
    return False
