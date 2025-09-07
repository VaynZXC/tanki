from __future__ import annotations

from pathlib import Path
from typing import Tuple, Optional, Iterable, Callable

import pyautogui
import numpy as np
import cv2
from loguru import logger

from wotbot.config import load_launcher_config, load_game_config
from wotbot.win.window_finder import find_launcher_hwnd_by_titles, find_game_hwnd_by_titles

# Optional external provider to avoid repeated window searches
_game_rect_provider: Optional[Callable[[], Optional[Tuple[int, int, int, int]]]] = None

def set_game_rect_provider(provider: Callable[[], Optional[Tuple[int, int, int, int]]]) -> None:
    global _game_rect_provider
    _game_rect_provider = provider

def clear_game_rect_provider() -> None:
    global _game_rect_provider
    _game_rect_provider = None


def _get_launcher_rect() -> Optional[Tuple[int, int, int, int]]:
    try:
        import win32gui
        cfg = load_launcher_config()
        hwnd = find_launcher_hwnd_by_titles(cfg.window_title_patterns)
        if not hwnd:
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return left, top, right - left, bottom - top
    except Exception as exc:
        logger.debug(f"_get_launcher_rect failed: {exc}")
        return None


def _get_game_rect() -> Optional[Tuple[int, int, int, int]]:
    # Use custom provider if available (to leverage cached HWND)
    if _game_rect_provider is not None:
        try:
            rect = _game_rect_provider()
            if rect is not None:
                return rect
        except Exception as exc:
            logger.debug(f"custom game rect provider failed: {exc}")
    try:
        import win32gui
        cfg = load_game_config()
        hwnd = find_game_hwnd_by_titles(cfg.window_title_patterns)
        if not hwnd:
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return left, top, right - left, bottom - top
    except Exception as exc:
        logger.debug(f"_get_game_rect failed: {exc}")
        return None


def _region_for_panel(panel: str, rect: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    l, t, w, h = rect
    if panel == "left":
        return (l, t, int(w * 0.46), h)
    if panel == "right":
        return (l + int(w * 0.46), t, int(w * 0.54), h)
    return (l, t, w, h)


def locate_template_on_launcher(template_path: Path, *, panel: str = "any", confidence: float = 0.86, grayscale: bool = False) -> Optional[Tuple[int, int]]:
    rect = _get_launcher_rect()
    if rect is None:
        return None
    region = _region_for_panel(panel, rect)
    try:
        box = pyautogui.locateOnScreen(str(template_path), region=region, confidence=confidence, grayscale=grayscale)
        if not box:
            return None
        center = pyautogui.center(box)
        return center.x, center.y
    except Exception as exc:
        logger.debug(f"locate_template_on_launcher error: {exc}")
        return None


def click_template_on_launcher(template_path: Path, *, panel: str = "any", confidence: float = 0.86, grayscale: bool = False, dx: int = 0, dy: int = 0) -> bool:
    pos = locate_template_on_launcher(template_path, panel=panel, confidence=confidence, grayscale=grayscale)
    if not pos:
        return False
    x, y = pos
    pyautogui.click(x + dx, y + dy)
    logger.info(f"Clicked template {template_path.name} at {(x, y)}")
    return True


def locate_template_on_game(template_path: Path, *, confidence: float = 0.86, grayscale: bool = False) -> Optional[Tuple[int, int]]:
    rect = _get_game_rect()
    if rect is None:
        return None
    l, t, w, h = rect
    try:
        box = pyautogui.locateOnScreen(str(template_path), region=(l, t, w, h), confidence=confidence, grayscale=grayscale)
        if not box:
            return None
        center = pyautogui.center(box)
        return center.x, center.y
    except Exception:
        # errors during locate are expected sometimes; return None silently to avoid log spam
        return None


def click_template_on_game(template_path: Path, *, confidence: float = 0.86, grayscale: bool = False, dx: int = 0, dy: int = 0) -> bool:
    pos = locate_template_on_game(template_path, confidence=confidence, grayscale=grayscale)
    if not pos:
        return False
    x, y = pos
    pyautogui.click(x + dx, y + dy)
    logger.info(f"Clicked game template {template_path.name} at {(x, y)}")
    return True


def _screenshot_game_bgr() -> Optional[Tuple[np.ndarray, Tuple[int, int]]]:
    rect = _get_game_rect()
    if rect is None:
        return None
    l, t, w, h = rect
    # Prefer DXGI capture (avoids black frames from DirectX apps); fallback to pyautogui
    try:
        import dxcam  # type: ignore
        cam = dxcam.create(output_idx=0, output_color="BGRA")
        frame = cam.grab(region=(l, t, l + w, t + h))
        if frame is not None:
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        else:
            img = pyautogui.screenshot(region=(l, t, w, h))
            arr = np.array(img)
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception:
        img = pyautogui.screenshot(region=(l, t, w, h))
        arr = np.array(img)
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return bgr, (l, t)


def _imread_bgr(path: Path) -> Optional[np.ndarray]:
    try:
        data = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if data is None:
            # fallback to imread
            data = cv2.imread(str(path), cv2.IMREAD_COLOR)
        return data
    except Exception as exc:
        logger.debug(f"imread error for {path.name}: {type(exc).__name__}: {exc}")
        return None


def click_template_on_game_cv(
    template_path: Path,
    *,
    confidences: Iterable[float] = (0.86, 0.82, 0.78),
    scales: Iterable[float] = (1.00, 0.97, 1.03),
) -> bool:
    shot = _screenshot_game_bgr()
    if shot is None:
        return False
    scene_bgr, (left, top) = shot
    scene_gray = cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2GRAY)

    templ_bgr = _imread_bgr(template_path)
    if templ_bgr is None:
        return False
    templ_gray_orig = cv2.cvtColor(templ_bgr, cv2.COLOR_BGR2GRAY)

    H, W = scene_gray.shape[:2]
    best_val = -1.0
    best_pt = None
    best_wh = None

    for s in scales:
        th = int(round(templ_gray_orig.shape[0] * s))
        tw = int(round(templ_gray_orig.shape[1] * s))
        if th < 8 or tw < 8:
            continue
        if th >= H or tw >= W:
            continue
        templ_gray = cv2.resize(templ_gray_orig, (tw, th), interpolation=cv2.INTER_AREA)
        try:
            res = cv2.matchTemplate(scene_gray, templ_gray, cv2.TM_CCOEFF_NORMED)
        except Exception as exc:
            logger.debug(f"matchTemplate error: {type(exc).__name__}: {exc}")
            continue
        minVal, maxVal, minLoc, maxLoc = cv2.minMaxLoc(res)
        if maxVal > best_val:
            best_val = maxVal
            best_pt = maxLoc
            best_wh = (tw, th)

    if best_pt is None or best_wh is None:
        return False

    for thr in confidences:
        if best_val >= thr:
            x, y = best_pt
            tw, th = best_wh
            cx = left + x + tw // 2
            cy = top + y + th // 2
            pyautogui.click(cx, cy)
            logger.info(
                f"Clicked game template (cv2) {template_path.name} at {(cx, cy)} score={best_val:.3f}"
            )
            return True

    logger.debug(
        f"Template {template_path.name} best score={best_val:.3f} below thresholds"
    )
    return False
