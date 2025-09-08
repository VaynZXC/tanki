from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
import ctypes

import pyautogui
import numpy as np
import cv2
from PIL import Image
from loguru import logger
try:
    import keyboard  # global hotkeys
    _HAVE_KBD = True
except Exception:
    _HAVE_KBD = False

from wotbot.vision.state_classifier import PHashStateClassifier
from wotbot.vision.template_finder import click_template_on_game, click_template_on_game_cv, locate_template_on_game, set_game_rect_provider
from wotbot.config import load_game_config
from wotbot.win.window_finder import find_game_hwnd_by_titles
from wotbot.win.window_actions import restore_and_focus_window
from wotbot.win.driver_input import (
    driver_is_available,
    driver_press_scan,
    driver_press_scan_ephemeral,
    SC_ENTER,
    SC_ESCAPE,
    SC_SPACE,
)

import win32api
import win32con
import win32gui
import psutil


THINK_DELAY = 0.4
# Сколько раз подряд нужно увидеть одну сцену, чтобы считать, что зависли
STUCK_THRESHOLD = 10
# Allowed scene sets by phase
PHASE_PRE_ALLOWED = {"game_loading", "game_cutscena", "game_tutorial1"}
PHASE_TUTORIAL_ALLOWED = {"game_tutorial1", "game_tutorial2", "game_tutorial_menu", "game_tutorial_menu_conf"}
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
KEYEVENTF_SCANCODE = 0x0008

# Cache for game window handle
_GAME_HWND_CACHE: int | None = None

# ctypes structures for SendInput
try:
	ULONG_PTR = ctypes.wintypes.ULONG_PTR  # type: ignore[attr-defined]
except Exception:
	ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

class KEYBDINPUT(ctypes.Structure):
	_fields_ = [
		("wVk", ctypes.c_ushort),
		("wScan", ctypes.c_ushort),
		("dwFlags", ctypes.c_ulong),
		("time", ctypes.c_ulong),
		("dwExtraInfo", ULONG_PTR),
	]

class _INPUTUNION(ctypes.Union):
	_fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
	_anonymous_ = ("u",)
	_fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]

SendInput = ctypes.windll.user32.SendInput

# Common scan codes
SC_ENTER = 0x1C
SC_ESCAPE = 0x01
SC_SPACE = 0x39


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run WoT in-game onboarding skipper")
    ap.add_argument("--dataset", type=str, default="dataset", help="Root dataset with scene templates")
    ap.add_argument("--templates", type=str, default="dataset/templates", help="Folder with small UI templates")
    ap.add_argument("--max-secs", type=int, default=120, help="Max seconds to try")
    ap.add_argument("--vision-snapshots", action="store_true", help="Save what bot sees from the game window")
    ap.add_argument("--vision-snap-interval", type=float, default=5.0, help="Seconds between snapshots")
    ap.add_argument("--hotkey-stop", type=str, default="f10", help="Global hotkey to stop the script")
    ap.add_argument("--hotkey-pause", type=str, default="f9", help="Global hotkey to pause/resume actions")
    ap.add_argument("--no-hotkeys", action="store_true", help="Disable global hotkeys handling")
    ap.add_argument("--result-file", type=str, default="", help="Path to write selected reward tank ids (comma-separated)")
    return ap.parse_args()


def _get_game_hwnd() -> int | None:
    # Cache the game HWND to avoid repeated title searches on every tick
    global _GAME_HWND_CACHE
    try:
        if _GAME_HWND_CACHE and win32gui.IsWindow(_GAME_HWND_CACHE):
            return _GAME_HWND_CACHE
    except Exception:
        pass
    cfg = load_game_config()
    hwnd = find_game_hwnd_by_titles(cfg.window_title_patterns)
    _GAME_HWND_CACHE = hwnd
    return hwnd


def _focus_game() -> int | None:
    hwnd = _get_game_hwnd()
    if not hwnd:
        return None
    ok = restore_and_focus_window(hwnd)
    if not ok:
        logger.debug("Не удалось активировать окно игры")
    return hwnd


def _game_region() -> tuple[int, int, int, int] | None:
    hwnd = _get_game_hwnd()
    if not hwnd:
        return None
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    return (l, t, r - l, b - t)


# ====== Scroll memory (persisted between runs) ======
_SCROLL_PREFS_DIR = Path("accounts_final") / "tank_scrolls"

def _read_scroll_count(tank_id: str) -> int:
    try:
        p = _SCROLL_PREFS_DIR / f"{tank_id}.txt"
        if p.exists():
            raw = p.read_text(encoding="utf-8").strip()
            return int(raw or 0)
    except Exception:
        return 0
    return 0


def _write_scroll_count(tank_id: str, count: int) -> None:
    try:
        _SCROLL_PREFS_DIR.mkdir(parents=True, exist_ok=True)
        p = _SCROLL_PREFS_DIR / f"{tank_id}.txt"
        old = _read_scroll_count(tank_id)
        new_val = count if old == 0 else min(old, count)
        p.write_text(str(max(0, new_val)), encoding="utf-8")
    except Exception:
        pass


def _scroll_to_top(max_steps: int = 40, step_amount: int = 600) -> None:
    """Aggressively scroll up to the start of the list before measurements."""
    region = _game_region()
    if not region:
        return
    l, t, w, h = region
    cx, cy = l + w // 2, t + int(h * 0.75)
    safe_x = max(l + 20, cx - 200)
    try:
        pyautogui.moveTo(safe_x, cy)
        for _ in range(max_steps):
            pyautogui.scroll(step_amount)
            time.sleep(0.005)
    except Exception:
        pass


def _move_to_scroll_anchor() -> tuple[int, int] | None:
    region = _game_region()
    if not region:
        return None
    l, t, w, h = region
    cx, cy = l + w // 2, t + int(h * 0.75)
    safe_x = max(l + 20, cx - 200)
    try:
        pyautogui.moveTo(safe_x, cy)
    except Exception:
        return None
    return safe_x, cy


def _scroll_step_down(units: int = 200) -> None:
    anchor = _move_to_scroll_anchor()
    if not anchor:
        return
    try:
        pyautogui.scroll(-abs(units))
    except Exception:
        pass


def _rapid_scroll_down(steps: int, units: int = 200, delay: float = 0.015) -> None:
    """Fast consecutive scrolls: anchor once, then scroll N steps with tiny delay."""
    anchor = _move_to_scroll_anchor()
    if not anchor:
        return
    try:
        for _ in range(max(0, steps)):
            pyautogui.scroll(-abs(units))
            time.sleep(max(0.0, delay))
    except Exception:
        pass


def _locate_icon(icon: Path) -> tuple[int, int] | None:
    pos = locate_template_on_game(icon, confidence=0.86)
    if not pos:
        pos = locate_template_on_game(icon, confidence=0.82, grayscale=True)
    # Фолбэк: обрезанная версия, если перекрывает описание
    if not pos:
        try:
            obrez = icon.with_name(f"{icon.stem}_obrez{icon.suffix}")
            if obrez.exists():
                pos = locate_template_on_game(obrez, confidence=0.86)
                if not pos:
                    pos = locate_template_on_game(obrez, confidence=0.82, grayscale=True)
        except Exception:
            pass
    return pos


def _is_tank_selected(templates_dir: Path, tank_id: str) -> bool:
    """Проверяем маркер выбранного танка: <tank>_v.png"""
    try:
        sel = templates_dir / f"{tank_id}_v.png"
        if not sel.exists():
            return False
        pos = locate_template_on_game(sel, confidence=0.86)
        if not pos:
            pos = locate_template_on_game(sel, confidence=0.82, grayscale=True)
        return pos is not None
    except Exception:
        return False


def _click_tank_icon_by_id(templates_dir: Path, tank_id: str) -> bool:
    """Кликаем по иконке танка (учитывая *_obrez)."""
    try:
        icon = templates_dir / f"{tank_id}.png"
        pos = _locate_icon(icon)
        if not pos:
            return False
        x, y = pos
        _click_many(x, y, times=2, interval=0.06)
        return True
    except Exception:
        return False


def _find_and_click_tank_by_memory(tank_id: str, icon: Path, max_steps: int = 30, step_units: int = 200) -> tuple[bool, int]:
    """Return (found, used_steps). Uses saved step count if exists, otherwise measures by fixed steps."""
    used = 0
    saved = _read_scroll_count(tank_id)
    if saved > 0:
        _scroll_to_top()
        _rapid_scroll_down(saved, units=step_units, delay=0.015)
        used = saved
        pos = _locate_icon(icon)
        if pos:
            # Pause to stop any inertial scrolling, then re-locate to avoid stale coordinates
            time.sleep(2.0)
            pos2 = _locate_icon(icon) or pos
            pyautogui.moveTo(*pos2)
            time.sleep(0.10)
            _click_many(pos2[0], pos2[1], times=2, interval=0.06)
            return True, used
        # fallthrough to measurement if not found

    # Measure from top
    _scroll_to_top()
    used = 0
    # prepare anchor once for faster scrolling during measure
    _move_to_scroll_anchor()
    for _ in range(max(1, max_steps)):
        pos = _locate_icon(icon)
        if pos:
            time.sleep(2.0)
            pos2 = _locate_icon(icon) or pos
            pyautogui.moveTo(*pos2)
            time.sleep(0.10)
            _click_many(pos2[0], pos2[1], times=2, interval=0.06)
            try:
                _write_scroll_count(tank_id, used)
            except Exception:
                pass
            return True, used
        try:
            pyautogui.scroll(-abs(step_units))
        except Exception:
            pass
        used += 1
        time.sleep(0.015)
    return False, used


def _click_center_of_game() -> None:
    region = _game_region()
    if not region:
        return
    l, t, w, h = region
    cx, cy = l + w // 2, t + h // 2
    pyautogui.click(cx, cy)


def _double_click_center_of_game() -> None:
    region = _game_region()
    if not region:
        return
    l, t, w, h = region
    cx, cy = l + w // 2, t + h // 2
    pyautogui.doubleClick(cx, cy)


def _move_center_of_game() -> None:
    region = _game_region()
    if not region:
        return
    l, t, w, h = region
    cx, cy = l + w // 2, t + h // 2
    pyautogui.moveTo(cx, cy)


def _grab_game_image() -> Image.Image | None:
    region = _game_region()
    if region is None:
        return None
    l, t, w, h = region
    # Prefer DXGI duplication (dxcam) to avoid black frames from DirectX
    try:
        import dxcam  # type: ignore
        cam = dxcam.create(output_idx=0, output_color="BGRA")
        frame = cam.grab(region=(l, t, l + w, t + h))
        if frame is not None:
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)
    except Exception:
        pass
    # Fallback to GDI screenshot
    return pyautogui.screenshot(region=(l, t, w, h))


def _click_template_game_robust(path: Path) -> bool:
    # Переведено на агрессивный режим клика
    return _click_template_aggressive(path, timeout=1.6)


def _click_many(x: int | float, y: int | float, times: int = 2, interval: float = 0.05) -> None:
    for _ in range(max(1, times)):
        pyautogui.click(x, y)
        time.sleep(max(0.0, interval))


def _click_reward_aggressive(path: Path, timeout: float = 1.8) -> bool:
    """Быстрый, агрессивный клик по 'Получить награду':
    - пониженные пороги
    - двойной клик при нахождении
    - cv2 fallback с расширенными шкалами
    """
    _focus_game()
    t0 = time.time()
    while time.time() - t0 < timeout:
        pos = locate_template_on_game(path, confidence=0.83)
        if not pos:
            pos = locate_template_on_game(path, confidence=0.78, grayscale=True)
        if pos:
            x, y = pos
            _click_many(x, y, times=2, interval=0.06)
            return True
        if click_template_on_game_cv(path, confidences=(0.78, 0.74, 0.70), scales=(1.00, 0.97, 1.03, 0.94)):
            return True
        time.sleep(0.06)
    return False


def _click_template_aggressive(path: Path, timeout: float = 1.6) -> bool:
    """Агрессивный клик по любому UI-шаблону (двойной клик, пониженные пороги, cv2 fallback)."""
    _focus_game()
    t0 = time.time()
    while time.time() - t0 < timeout:
        pos = locate_template_on_game(path, confidence=0.86)
        if not pos:
            pos = locate_template_on_game(path, confidence=0.82, grayscale=True)
        if not pos:
            pos = locate_template_on_game(path, confidence=0.78, grayscale=True)
        if pos:
            x, y = pos
            _click_many(x, y, times=2, interval=0.06)
            return True
        if click_template_on_game_cv(path, confidences=(0.86, 0.82, 0.78, 0.74), scales=(1.00, 0.97, 1.03, 0.94)):
            return True
        time.sleep(0.06)
    return False


def _fast_retry_click(path: Path, timeout: float = 2.0, interval: float = 0.12) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        pos = locate_template_on_game(path, confidence=0.86)
        if not pos:
            pos = locate_template_on_game(path, confidence=0.82, grayscale=True)
        if pos:
            x, y = pos
            pyautogui.click(x, y)
            return True
        # fallback: try cv click (no pos)
        if click_template_on_game_cv(path):
            return True
        time.sleep(interval)
    return False


def _post_vk_to_hwnd(hwnd: int, vk: int) -> None:
    user32 = ctypes.windll.user32
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
    time.sleep(0.02)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, 0)


def _send_scancode(scan: int, repeats: int = 1) -> None:
    for _ in range(repeats):
        down = INPUT()
        down.type = 1
        down.ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE, 0, 0)
        up = INPUT()
        up.type = 1
        up.ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | win32con.KEYEVENTF_KEYUP, 0, 0)
        SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
        time.sleep(0.02)
        SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        time.sleep(0.03)


def _send_vk(vk: int, repeats: int = 1) -> None:
    for _ in range(repeats):
        win32api.keybd_event(vk, 0, 0, 0)
        time.sleep(0.03)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)


def _press_game_key(vk: int) -> None:
    hwnd = _focus_game()
    # Сначала драйвер, затем WinAPI/SendInput/WM_* фолбэки
    scan = SC_ENTER if vk == win32con.VK_RETURN else SC_ESCAPE if vk == win32con.VK_ESCAPE else SC_SPACE if vk == win32con.VK_SPACE else 0
    sent = False
    if scan:
        if driver_press_scan_ephemeral(scan, repeats=1, e0=False):
            sent = True
        elif driver_is_available() and driver_press_scan(scan, repeats=1, e0=False):
            sent = True
    if not sent and scan:
        try:
            _send_scancode(scan, repeats=1)
            sent = True
        except Exception:
            pass
    if not sent:
        try:
            _send_vk(vk, repeats=1)
            sent = True
        except Exception:
            pass
    if not sent and hwnd:
        try:
            _post_vk_to_hwnd(hwnd, vk)
        except Exception:
            pass


def _close_game() -> None:
    """Пытаемся закрыть ТОЛЬКО окно игры, не трогая активное приложение.
    Последовательно: WM_SYSCOMMAND/SC_CLOSE -> WM_CLOSE -> terminate/kill по PID.
    """
    hwnd = _get_game_hwnd()
    if not hwnd:
        return
    try:
        # 1) Попросить корректное закрытие через системную команду
        try:
            win32gui.PostMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_CLOSE, 0)
        except Exception:
            pass
        t_end = time.time() + 5.0
        while time.time() < t_end:
            if not win32gui.IsWindow(hwnd):
                break
            time.sleep(0.2)

        # 2) Если окно всё ещё живо — отправим WM_CLOSE напрямую
        if win32gui.IsWindow(hwnd):
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
            t_end2 = time.time() + 3.0
            while time.time() < t_end2:
                if not win32gui.IsWindow(hwnd):
                    break
                time.sleep(0.2)

        # 3) Жёсткое завершение процесса окна, если всё ещё существует
        if win32gui.IsWindow(hwnd):
            try:
                _, pid = win32gui.GetWindowThreadProcessId(hwnd)
                if pid:
                    proc = psutil.Process(pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.5)
                    except Exception:
                        proc.kill()
            except Exception:
                pass
    finally:
        # Сбросим кеш HWND, чтобы не держать устаревшую ручку
        global _GAME_HWND_CACHE
        _GAME_HWND_CACHE = None

def main() -> None:
    args = parse_args()
    dataset_root = Path(args.dataset)
    templates_dir = Path(args.templates)
    vision_dir = Path("vision_snaps")
    if args.vision_snapshots:
        vision_dir.mkdir(parents=True, exist_ok=True)

    # No persistent driver init: use ephemeral sends only

    # Provide cached game rect to template finder to avoid repeated window searches
    set_game_rect_provider(_game_region)

    clf = PHashStateClassifier(dataset_root)
    total = clf.load()
    logger.info(f"Loaded {total} templates for scenes")

    t0 = time.time()
    last_snap = 0.0
    last_tick = 0.0
    # phases: 'pre' -> 'tutorial' -> 'post'
    phase = 'pre'
    classify_paused = False
    post_stage = 0  # forward-only steps after tutorial
    reward_click_time = 0.0  # time of last 'Получить награду' click
    final_wait_start = 0.0   # time when 'game_ungar' first seen
    reached_ungar = False
    # hotkeys state
    stop_requested = False
    user_paused = False
    chosen_tanks: list[str] = []
    # детектор зависаний
    last_state_seen: str | None = None
    state_repeat_count: int = 0
    if _HAVE_KBD and not args.no_hotkeys:
        try:
            keyboard.add_hotkey(args.hotkey_stop, lambda: globals().update() or None)
        except Exception:
            pass
        # Use closures for flags without threads
        def _req_stop():
            nonlocal stop_requested
            stop_requested = True
        def _toggle_pause():
            nonlocal user_paused
            user_paused = not user_paused
            logger.info(f"Manual pause={'ON' if user_paused else 'OFF'}")
        try:
            keyboard.add_hotkey(args.hotkey_stop, _req_stop)
            keyboard.add_hotkey(args.hotkey_pause, _toggle_pause)
            logger.info(f"Hotkeys: stop={args.hotkey_stop}, pause={args.hotkey_pause}")
        except Exception:
            logger.info("Global hotkeys not available (no admin or keyboard module)")
    while time.time() - t0 < args.max_secs:
        # allow user stop/pause
        if stop_requested:
            logger.info("Stop requested via hotkey")
            break
        if user_paused:
            time.sleep(0.2)
            continue
        # Опрос сцены раз в 1 секунду
        now = time.time()
        if now - last_tick < 1.0:
            time.sleep(0.05)
            continue
        last_tick = now

        region = _game_region()
        if region is None:
            continue
        img = _grab_game_image()
        if img is None:
            # if capture failed, retry shortly
            time.sleep(0.1)
            continue
        if args.vision_snapshots and (time.time() - last_snap) >= max(0.5, args.vision_snap_interval):
            ts = time.strftime("%Y%m%d_%H%M%S")
            out = vision_dir / f"game_{ts}.png"
            try:
                img.save(out)
                logger.info(f"Saved vision snapshot: {out}")
            except Exception as exc:
                logger.debug(f"Save snapshot failed: {exc}")
            last_snap = time.time()
        state = None
        match = None
        if not classify_paused:
            match = clf.classify(img)
            if not match:
                continue
            state = match.state
            # Normalize aliases that may come without 'game_' prefix
            if not state.startswith("game_"):
                alias_prefixes = ("nagrada", "tutorial")
                alias_names = {
                    "ungar",
                    "loading",
                    "cutscena",
                    "video",
                    "vibor_tanka",
                }
                if state.startswith(alias_prefixes) or state in alias_names:
                    state = f"game_{state}"
                else:
                    logger.debug(f"Skip non-game state: {state}")
                    continue
            # Финал: как только попали в post-фазу — принимаем финальные сцены
            if state in {'game_ungar', 'game_nagrada_code'}:
                if phase != 'post':
                    logger.debug("Ignore final state outside post phase")
                    continue
                if post_stage < 7:
                    post_stage = 7
                # не continue — позволим циклу дойти до блока пост-этапа 7
            # Если туториала нет и сразу показывается ролик — переходим в post немедленно
            if state == 'game_video' and phase != 'post':
                phase = 'post'
                post_stage = 0
                logger.info("Detected game_video in pre/tutorial -> switching to post phase")
            # filter states by phase
            if phase == 'pre' and state not in PHASE_PRE_ALLOWED:
                logger.debug(f"Filtered by phase(pre): {state}")
                continue
            if phase == 'tutorial' and state not in PHASE_TUTORIAL_ALLOWED:
                logger.debug(f"Filtered by phase(tutorial): {state}")
                continue
            logger.info(f"Scene[{phase}]: {state} (dist={match.distance})")

            # ======= STUCK DETECTION =======
            if state == last_state_seen:
                state_repeat_count += 1
            else:
                last_state_seen = state
                state_repeat_count = 1

            if state_repeat_count >= STUCK_THRESHOLD:
                logger.warning(f"Stuck detected on '{state}' (seen {state_repeat_count}x) -> recovery")
                # Сбросим счётчик, чтобы не зациклиться при том же кадре
                state_repeat_count = 0
                # Восстановительные действия по сценам
                if state == 'game_tutorial1':
                    _press_game_key(win32con.VK_RETURN)
                    time.sleep(0.2)
                elif state == 'game_cutscena':
                    _press_game_key(win32con.VK_ESCAPE)
                    time.sleep(0.2)
                elif state == 'game_vibor_tanka':
                    # 1) Если танк уже выбран — подтверждаем Enter
                    if _is_tank_selected(templates_dir, 'is7') or _is_tank_selected(templates_dir, 'fv4005'):
                        _press_game_key(win32con.VK_RETURN)
                        time.sleep(0.2)
                    else:
                        # 2) Иначе кликаем по иконке: сначала тот, что ожидается стадией, потом другой
                        preferred_first = 'is7'
                        if phase == 'post' and post_stage >= 40:
                            preferred_first = 'fv4005'
                        order = [preferred_first, 'fv4005' if preferred_first == 'is7' else 'is7']
                        clicked = False
                        for tank_id in order:
                            if _click_tank_icon_by_id(templates_dir, tank_id):
                                clicked = True
                                break
                        # если всё ещё не получилось — мягкий Enter как фолбэк
                        if not clicked:
                            _press_game_key(win32con.VK_RETURN)
                elif state == 'game_loading':
                    # установить курсор в точку якоря скролла (как перед прокруткой)
                    _move_to_scroll_anchor()
                # после восстановления — переходим к следующей итерации цикла
                time.sleep(0.2)
                continue

        if state in {"game_loading"}:
            # после прохождения game_tutorial1 блокируем game_loading
            if phase == 'tutorial':
                continue
            time.sleep(0.3)
            continue

        if (not classify_paused and state in {"game_cutscena", "game_tutorial1"}):
            logger.info("Planned action: press Enter")
            time.sleep(THINK_DELAY)
            _press_game_key(win32con.VK_RETURN)
            if state == 'game_tutorial1':
                phase = 'tutorial'
                # Механический скип сразу после Enter: ESC -> клик skip -> подтверждение
                time.sleep(3.0)
                _press_game_key(win32con.VK_ESCAPE)
                time.sleep(0.5)
                _move_center_of_game()
                btn_main = templates_dir / "skip_tutorial_btn1.png"
                btn_alt = templates_dir / "skip_tutorial_btn1_1.png"
                def try_click_skip() -> bool:
                    # Агрессивный общий клик
                    if _click_template_aggressive(btn_main, timeout=1.2):
                        return True
                    if _click_template_aggressive(btn_alt, timeout=1.2):
                        return True
                    return False
                try_click_skip()
                time.sleep(0.15)
                btn_conf = templates_dir / "skip_tutorial_btn2.png"
                _click_template_aggressive(btn_conf, timeout=1.2)
                # пауза классификации пока не подтвердим
                classify_paused = True
            continue

        # Удаляем возвраты: после начала туториала не возвращаемся к ESC ещё раз

        if not classify_paused and state == "game_tutorial_menu":
            logger.info("Planned action: click skip_tutorial_btn1")
            time.sleep(THINK_DELAY)
            btn = templates_dir / "skip_tutorial_btn1.png"
            if not _click_template_aggressive(btn, timeout=1.4):
                logger.warning("skip_tutorial_btn1 not found")
            time.sleep(0.2)
            continue

        # Когда классификация выключена — продолжаем механически давить подтверждение
        if classify_paused:
            btn_conf = templates_dir / "skip_tutorial_btn2.png"
            _click_template_aggressive(btn_conf, timeout=1.4)
            # включаем классификацию и переходим в post
            classify_paused = False
            phase = 'post'
            time.sleep(0.2)
            continue

        if state == "game_tutorial_menu_conf":
            logger.info("Planned action: click skip_tutorial_btn2")
            time.sleep(THINK_DELAY)
            btn = templates_dir / "skip_tutorial_btn2.png"
            if not _click_template_aggressive(btn, timeout=1.4):
                logger.warning("skip_tutorial_btn2 not found")
            time.sleep(0.2)
            # после подтверждения пропуска — блокируем tutorial сцены
            phase = 'post'
            continue

        # Post phase pipeline (mechanics first)
        if phase == 'post':
            # Stage 0: ждём game_video и жмём ESC, затем Enter (фолбэк). Повторяем до смены сцены.
            if post_stage == 0:
                m = clf.classify(img)
                if m and m.state == 'game_video':
                    logger.info("Post: game_video -> press ESC (+Enter fallback)")
                    _press_game_key(win32con.VK_ESCAPE)
                    time.sleep(0.15)
                    _press_game_key(win32con.VK_RETURN)
                    # проверим, сменилась ли сцена через короткое ожидание
                    time.sleep(0.4)
                    m2 = clf.classify(_grab_game_image() or img)
                    if m2 and m2.state == 'game_video':
                        # попробуем ещё раз
                        _press_game_key(win32con.VK_ESCAPE)
                        time.sleep(0.15)
                        _press_game_key(win32con.VK_RETURN)
                        time.sleep(0.15)
                        # попробуем SPACE как альтернативный пропуск
                        _press_game_key(win32con.VK_SPACE)
                        time.sleep(0.1)
                        _press_game_key(win32con.VK_RETURN)
                        time.sleep(0.2)
                    post_stage = 1
                    time.sleep(0.2)
                    continue

            # Stage 1: первый экран наград — жмём Enter, но переходим на stage 2 только когда видим game_vibor_tanka
            if post_stage == 1:
                m = clf.classify(img)
                if m and m.state == 'game_vibor_tanka':
                    post_stage = 2
                    continue
                logger.info("Post: reward screen1 -> press Enter x2")
                _press_game_key(win32con.VK_RETURN)
                time.sleep(0.15)
                _press_game_key(win32con.VK_RETURN)
                time.sleep(0.2)
                # если по-прежнему не ушли с ролика — ещё один Enter/ESC как фолбэк
                if m and m.state == 'game_video':
                    _press_game_key(win32con.VK_RETURN)
                    time.sleep(0.1)
                    _press_game_key(win32con.VK_ESCAPE)
                time.sleep(0.2)
                # остаёмся на stage 1 до смены сцены
                continue

            # Stage 2: выбрать танк is7 (фиксированные шаги 200px + память)
            if post_stage == 2:
                m = clf.classify(img)
                # защита: если всё ещё на game_nagrada_screen1 — вернуться на stage 1 и снова жать Enter
                if m and m.state == 'game_nagrada_screen1':
                    _press_game_key(win32con.VK_RETURN)
                    time.sleep(0.15)
                    _press_game_key(win32con.VK_RETURN)
                    post_stage = 1
                    continue
                # продолжаем только на экране выбора танка
                if not (m and m.state == 'game_vibor_tanka'):
                    continue
                tank_icon = templates_dir / 'is7.png'
                ok, _ = _find_and_click_tank_by_memory('is7', tank_icon, max_steps=60, step_units=200)
                if not ok:
                    continue
                try:
                    chosen_tanks.append('is7')
                except Exception:
                    pass
                # выбрали первый танк; перейти к клику награды с Enter-подтверждением
                post_stage = 31
                continue

            # 3) Нажать 'получить награду'
            # (этот блок достигнется на следующем тике)
            # оставлен ниже как отдельный этап

        if phase == 'post' and post_stage == 31:
            # 31: подтвердить выбор танка Enter один раз
            _press_game_key(win32con.VK_RETURN)
            post_stage = 32
            continue

        if phase == 'post' and post_stage == 32:
            # 32: дождаться 'game_nagrada_screen2' и кликнуть 'получить награду' немедленно
            m = clf.classify(img)
            if not (m and m.state == 'game_nagrada_screen2'):
                # Дополнительно: если застряли на первом экране награды — жмём Enter каждый раз
                if m and m.state == 'game_nagrada_screen1':
                    _press_game_key(win32con.VK_RETURN)
                continue
            reward_btn = templates_dir / 'poluchit_nagradu.png'
            if _click_reward_aggressive(reward_btn, timeout=1.8) or _fast_retry_click(reward_btn, timeout=1.2) or _click_template_game_robust(reward_btn):
                reward_click_time = time.time()
                post_stage = 33
            continue

        if phase == 'post' and post_stage == 33:
            # 33: пауза 5 секунд после клика по кнопке, затем Enter (x2)
            if (time.time() - reward_click_time) < 5.0:
                # опционально можем дополнительно поддерживать фокус/повтор клика коротко
                reward_btn = templates_dir / 'poluchit_nagradu.png'
                _click_reward_aggressive(reward_btn, timeout=0.2)
                continue
            _press_game_key(win32con.VK_RETURN)
            time.sleep(0.15)
            _press_game_key(win32con.VK_RETURN)
            post_stage = 4
            continue

            # Второй танк: fv4005 — повторяем логику выбора
        if phase == 'post' and post_stage == 4:
            # 4: дождаться возврата на экран выбора танка
            m = clf.classify(img)
            if m and m.state == 'game_vibor_tanka':
                post_stage = 42
            continue

        if phase == 'post' and post_stage == 42:
            # Второй танк: fv4005 — фиксированные шаги 200px + память
            tank_icon2 = templates_dir / 'fv4005.png'
            ok2, _ = _find_and_click_tank_by_memory('fv4005', tank_icon2, max_steps=60, step_units=200)
            if not ok2:
                continue
            try:
                chosen_tanks.append('fv4005')
            except Exception:
                pass
            # выбрали второй танк; перейти к Enter + получить награду + Enter
            post_stage = 51
            continue

        if phase == 'post' and post_stage == 51:
            # 51: подтвердить второй танк Enter
            _press_game_key(win32con.VK_RETURN)
            post_stage = 52
            continue

        if phase == 'post' and post_stage == 52:
            # 52: дождаться 'game_nagrada_screen2' и кликнуть 'получить награду' немедленно
            m = clf.classify(img)
            if not (m and m.state == 'game_nagrada_screen2'):
                continue
            reward_btn = templates_dir / 'poluchit_nagradu.png'
            if _click_reward_aggressive(reward_btn, timeout=1.8) or _fast_retry_click(reward_btn, timeout=1.2) or _click_template_game_robust(reward_btn):
                reward_click_time = time.time()
                post_stage = 53
            continue

        if phase == 'post' and post_stage == 53:
            # 53: пауза 5 секунд после клика по кнопке, затем Enter (x2)
            if (time.time() - reward_click_time) < 5.0:
                reward_btn = templates_dir / 'poluchit_nagradu.png'
                _click_reward_aggressive(reward_btn, timeout=0.2)
                continue
            _press_game_key(win32con.VK_RETURN)
            time.sleep(0.15)
            _press_game_key(win32con.VK_RETURN)
            post_stage = 7
            continue

        # этап 6 больше не используется; финал начинается с этапа 7

        if phase == 'post' and post_stage == 7:
            # ждём финальную сцену (game_ungar или game_nagrada_code);
            # после её появления ждём 5 секунд и только затем закрываем игру
            m = clf.classify(img)
            if m and m.state in {'game_ungar', 'game_nagrada_code'}:
                if final_wait_start == 0.0:
                    final_wait_start = time.time()
                if (time.time() - final_wait_start) >= 5.0:
                    _close_game()
                    reached_ungar = True
                    break
                # пока ждём 5 секунд — не жмём Enter
                time.sleep(0.2)
                continue
            # если ещё не финальная сцена — продолжаем мягко жать Enter
            _press_game_key(win32con.VK_RETURN)
            time.sleep(0.4)
            continue

        # ждём следующий тик цикла (1 сек управляется вверху)

    logger.info("Game flow watcher finished")
    # Persist tank list if requested
    try:
        if args.result_file:
            Path(args.result_file).parent.mkdir(parents=True, exist_ok=True)
            uniq: list[str] = []
            seen: set[str] = set()
            for s in chosen_tanks:
                if s and s not in seen:
                    seen.add(s)
                    uniq.append(s)
            Path(args.result_file).write_text(",".join(uniq), encoding="utf-8")
    except Exception:
        pass
    # Успешно, только если дошли до game_ungar
    sys.exit(0 if reached_ungar else 2)


if __name__ == "__main__":
    main()
