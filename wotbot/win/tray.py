from __future__ import annotations

from typing import Iterable, Optional, Tuple
import time

import uiautomation as auto
from loguru import logger
import pyautogui
import win32gui
import numpy as np
import cv2
from pathlib import Path


def _expand_overflow_tray() -> Optional[auto.Control]:
    """Open the overflow tray popup if present and return its window/control.

    Works on Windows 10/11, opens popup with all hidden icons.
    """
    taskbar = auto.WindowControl(searchDepth=1, ClassName='Shell_TrayWnd')
    if not taskbar.Exists(2, 0.2):
        logger.debug("Taskbar (Shell_TrayWnd) not found")
        return None

    # Try 'Show hidden icons' button
    try:
        chevron_btn = taskbar.ButtonControl(Compare=lambda c, d: 'hidden icons' in c.Name.lower() or 'скрытых' in c.Name.lower())
        if chevron_btn.Exists(2, 0.2):
            chevron_btn.Click()
            time.sleep(0.3)
    except Exception:
        pass

    # Overflow window is typically 'NotifyIconOverflowWindow'
    overflow = auto.WindowControl(ClassName='NotifyIconOverflowWindow')
    if overflow.Exists(2, 0.2):
        return overflow

    # On some builds, a Pane is used
    overflow_pane = auto.PaneControl(Compare=lambda c, d: 'overflow' in c.Name.lower() or 'скрыт' in c.Name.lower())
    if overflow_pane.Exists(2, 0.2):
        return overflow_pane

    return None


def double_click_tray_icon_by_hints(name_hints: Iterable[str], max_icons: int = 30) -> bool:
    """Try to find a tray icon by accessible name hints and double click it.

    Searches both the visible tray and the overflow popup.
    """
    hints = [h.lower() for h in name_hints]

    # First, try visible tray toolbar
    try:
        taskbar = auto.WindowControl(searchDepth=1, ClassName='Shell_TrayWnd')
        if taskbar.Exists(2, 0.2):
            toolbar = taskbar.ToolBarControl(searchDepth=5)
            if toolbar.Exists(2, 0.2):
                count = 0
                for btn in toolbar.GetChildren():
                    try:
                        name = (btn.Name or '').lower()
                        if any(h in name for h in hints):
                            btn.DoubleClick()
                            return True
                        count += 1
                        if count >= max_icons:
                            break
                    except Exception:
                        continue
    except Exception as exc:
        logger.debug(f"Visible tray probe error: {exc}")

    # Then, try overflow tray
    overflow = _expand_overflow_tray()
    if overflow is None:
        logger.debug("Overflow tray not found")
        return False

    try:
        count = 0
        for ctrl in overflow.GetChildren():
            try:
                name = (ctrl.Name or '').lower()
                if any(h in name for h in hints):
                    ctrl.DoubleClick()
                    return True
                count += 1
                if count >= max_icons:
                    break
            except Exception:
                continue
    except Exception as exc:
        logger.debug(f"Overflow tray probe error: {exc}")

    return False


def double_click_first_tray_icon() -> bool:
    """Fallback: double click the first tray icon (visible, then overflow).

    WARNING: This may activate an unintended app if ordering changes.
    """
    # Visible tray first (UIA)
    try:
        taskbar = auto.WindowControl(searchDepth=1, ClassName='Shell_TrayWnd')
        if taskbar.Exists(2, 0.2):
            toolbar = taskbar.ToolBarControl(searchDepth=5)
            if toolbar.Exists(2, 0.2):
                for btn in toolbar.GetChildren():
                    try:
                        btn.DoubleClick()
                        return True
                    except Exception:
                        continue
    except Exception as exc:
        logger.debug(f"Visible tray first-click error: {exc}")

    # Overflow tray next (UIA)
    overflow = _expand_overflow_tray()
    if overflow is None:
        # Coordinate fallback near bottom-right of primary screen
        try:
            w, h = pyautogui.size()
            # try several candidate points near bottom-right
            candidates = []
            for dy in (10, 20, 30):
                for dx in (60, 90, 120, 150):
                    candidates.append((w - dx, h - dy))
            for (x, y) in candidates:
                try:
                    pyautogui.moveTo(x, y)
                    pyautogui.doubleClick()
                    return True
                except Exception:
                    continue
        except Exception as exc:
            logger.debug(f"Coordinate tray fallback error: {exc}")
        return False
    try:
        for ctrl in overflow.GetChildren():
            try:
                ctrl.DoubleClick()
                return True
            except Exception:
                continue
    except Exception as exc:
        logger.debug(f"Overflow tray first-click error: {exc}")
    return False


def open_first_overflow_icon_keyboard() -> bool:
    """Keyboard fallback: Win+B -> Enter (open overflow) -> Home -> Enter."""
    try:
        pyautogui.hotkey('win', 'b')
        time.sleep(0.25)
        pyautogui.press('enter')
        time.sleep(0.25)
        pyautogui.press('home')
        time.sleep(0.1)
        pyautogui.press('enter')
        return True
    except Exception as exc:
        logger.debug(f"Keyboard tray fallback error: {exc}")
        return False


def _get_taskbar_rect() -> Optional[Tuple[int, int, int, int]]:
    try:
        hwnd = win32gui.FindWindow('Shell_TrayWnd', None)
        if not hwnd:
            return None
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return (l, t, r - l, b - t)
    except Exception as exc:
        logger.debug(f"Get taskbar rect failed: {exc}")
        return None


def _locate_on_region(template_path: Path, region: Tuple[int, int, int, int], confidences=(0.86, 0.82, 0.78)) -> Optional[Tuple[int, int]]:
    try:
        box = pyautogui.locateOnScreen(str(template_path), region=region, confidence=confidences[0])
        if not box:
            box = pyautogui.locateOnScreen(str(template_path), region=region, confidence=confidences[1])
        if not box:
            box = pyautogui.locateOnScreen(str(template_path), region=region, confidence=confidences[2])
        if not box:
            return None
        center = pyautogui.center(box)
        return (center.x, center.y)
    except Exception:
        return None


def _locate_on_region_cv(template_path: Path, region: Tuple[int, int, int, int], confidences=(0.86, 0.82, 0.78), scales=(1.00, 0.97, 1.03)) -> Optional[Tuple[int, int]]:
    try:
        l, t, w, h = region
        shot = pyautogui.screenshot(region=(l, t, w, h))
        scene = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2GRAY)
        templ = cv2.imdecode(np.fromfile(str(template_path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if templ is None:
            templ = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
        if templ is None:
            return None
        H, W = scene.shape[:2]
        best_val = -1.0
        best_pt = None
        best_wh = None
        for s in scales:
            th = int(round(templ.shape[0] * s))
            tw = int(round(templ.shape[1] * s))
            if th < 6 or tw < 6 or th >= H or tw >= W:
                continue
            templ_s = cv2.resize(templ, (tw, th), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(scene, templ_s, cv2.TM_CCOEFF_NORMED)
            minVal, maxVal, minLoc, maxLoc = cv2.minMaxLoc(res)
            if maxVal > best_val:
                best_val = maxVal
                best_pt = maxLoc
                best_wh = (tw, th)
        if best_pt is None or best_wh is None:
            return None
        for thr in confidences:
            if best_val >= thr:
                x, y = best_pt
                tw, th = best_wh
                return (l + x + tw // 2, t + y + th // 2)
        return None
    except Exception as exc:
        logger.debug(f"cv locate error: {exc}")
        return None


def double_click_taskbar_icon_template(template_path: Path, *, right_fraction: float = 0.6) -> bool:
    """Locate template on the RIGHT part of the taskbar and double-click it."""
    rect = _get_taskbar_rect()
    if rect is None:
        logger.debug("Taskbar rect unavailable")
        return False
    l, t, w, h = rect
    rf = max(0.1, min(0.95, right_fraction))
    region = (l + int((1.0 - rf) * w), t, int(rf * w), h)
    pos = _locate_on_region(template_path, region)
    if not pos:
        pos = _locate_on_region_cv(template_path, region)
    if not pos:
        return False
    try:
        pyautogui.doubleClick(*pos)
        return True
    except Exception:
        return False
