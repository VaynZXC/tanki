from __future__ import annotations

import time
import win32con
import win32gui

from loguru import logger


SW_RESTORE = win32con.SW_RESTORE


def restore_and_focus_window(hwnd: int) -> bool:
    """Restore a minimized window and bring it to the foreground.

    Returns True if we believe the window is now foreground/visible.
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        logger.debug("restore_and_focus_window: invalid hwnd")
        return False

    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, SW_RESTORE)
        else:
            # Ensure shown
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        # Try to bring to foreground with a couple retries
        for _ in range(3):
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            time.sleep(0.1)

        is_visible = win32gui.IsWindowVisible(hwnd)
        logger.debug(f"restore_and_focus_window: visible={is_visible}")
        return is_visible
    except Exception as exc:
        logger.warning(f"restore_and_focus_window failed: {exc}")
        return False
