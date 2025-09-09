from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple, Optional

import time
import pyautogui
from PIL import Image
import pyperclip
import ctypes
from ctypes import wintypes
import uiautomation as auto

from loguru import logger

from wotbot.vision.state_classifier import PHashStateClassifier
from wotbot.launcher.ensure_visible import ensure_launcher_visible
from wotbot.win.window_finder import find_launcher_hwnd_by_titles
from wotbot.config import load_launcher_config
from wotbot.vision.template_finder import click_template_on_launcher, locate_template_on_launcher


STEP_DELAY = 0.2  # seconds between actions
MAX_SCROLL_TRIES = 5
HOVER_DX = 50  # reduced hover shift before scroll
TYPE_INTERVAL = 0.10  # seconds between characters when typing (fallback)
AVATAR_SCROLL_DX = 50  # pixels to the right from avatar for scroll

# WinAPI constants for SendInput Unicode typing
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1


@dataclass
class Credentials:
	email: str
	password: str


# Relative anchors (tune if needed)
AVATAR_RXY = (0.04, 0.12)
PLAY_BTN_RXY = (0.16, 0.90)
ADD_ACCOUNT_RXY = (0.16, 0.96)
# Scroll along the right edge of the left side panel so the list moves
LOGOUT_SCROLL_POINT_RXY = (0.42, 0.90)
LOGOUT_SCROLL_AMOUNT = -1600
ACCOUNT_CROSS_RXY = (0.46, 0.16)  # legacy fallback (kept but not used if template exists)
LOGOUT_CONF_CONTINUE_RXY = (0.42, 0.82)

# Templates (put small crops here if available)
LOGOUT_TEMPLATE = Path("dataset/templates/logout.png")
KRESTIK_TEMPLATE = Path("dataset/templates/krestik.png")
CONTINUE_TEMPLATE = Path("dataset/templates/continue.png")
EMAIL_TEMPLATE = Path("dataset/templates/email.png")
PASSWORD_TEMPLATE = Path("dataset/templates/password.png")
LOGIN_BTN_TEMPLATE = Path("dataset/templates/login_btn.png")
LOGIN_ERROR_TEMPLATE = Path("dataset/templates/login_error.png")
class LoginInvalidError(Exception):
    """Raised when login credentials are invalid (explicit UI error detected)."""
    pass

class GameStartTimeoutError(Exception):
    """Raised when the game client window did not appear within the expected time."""
    pass



try:
	ULONG_PTR = wintypes.ULONG_PTR  # type: ignore[attr-defined]
except AttributeError:
	ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
	_fields_ = [
		("wVk", wintypes.WORD),
		("wScan", wintypes.WORD),
		("dwFlags", wintypes.DWORD),
		("time", wintypes.DWORD),
		("dwExtraInfo", ULONG_PTR),
	]


class _INPUTUNION(ctypes.Union):
	_fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
	_anonymous_ = ("u",)
	_fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


SendInput = ctypes.windll.user32.SendInput


def _send_unicode_char(char: str) -> None:
	code = ord(char)
	down = INPUT()
	down.type = INPUT_KEYBOARD
	down.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)
	up = INPUT()
	up.type = INPUT_KEYBOARD
	up.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
	SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
	SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


def _type_unicode(text: str, label: str, obscure: bool = False) -> None:
	shown = ("*" * len(text)) if obscure else text
	logger.info(f"Ввод (UNICODE) в поле {label}: '{shown}' (len={len(text)})")
	pyautogui.hotkey("ctrl", "a")
	time.sleep(0.06)
	pyautogui.press("backspace")
	time.sleep(0.06)
	for ch in text:
		_send_unicode_char(ch)
		time.sleep(0.01)
	time.sleep(STEP_DELAY)


def _paste_clipboard(text: str, label: str, obscure: bool, verify: bool) -> bool:
	shown = ("*" * len(text)) if obscure else text
	logger.info(f"Ввод через буфер в поле {label}: '{shown}' (len={len(text)})")
	try:
		pyperclip.copy(text)
		logger.debug("Текст скопирован в буфер")
	except Exception as exc:
		logger.warning(f"Буфер обмена недоступен: {exc}")
		return False
	pyautogui.hotkey("ctrl", "a")
	time.sleep(0.06)
	pyautogui.press("backspace")
	time.sleep(0.06)
	pyautogui.hotkey("ctrl", "v")
	time.sleep(0.15)
	if not verify:
		return True
	# verify (email only)
	pyautogui.hotkey("ctrl", "a")
	time.sleep(0.05)
	pyautogui.hotkey("ctrl", "c")
	time.sleep(0.05)
	try:
		pasted = pyperclip.paste()
	except Exception as exc:
		logger.warning(f"Не удалось прочитать из буфера: {exc}")
		return False
	ok = pasted == text
	logger.debug(f"Проверка буфера для {label}: {'OK' if ok else 'FAIL'}")
	return ok


def _type_with_logging(text: str, label: str, obscure: bool = False) -> None:
	shown = ("*" * len(text)) if obscure else text
	logger.info(f"Ввод в поле {label}: '{shown}' (len={len(text)}) посимвольно с интервалом {TYPE_INTERVAL}s")
	pyautogui.hotkey("ctrl", "a")
	time.sleep(0.06)
	pyautogui.press("backspace")
	time.sleep(0.06)
	pyautogui.typewrite(text, interval=TYPE_INTERVAL)
	time.sleep(STEP_DELAY)


def _input_text_smart(text: str, label: str, obscure: bool = False) -> None:
	# 1) пробуем через буфер
	verify = (label != "password")
	if _paste_clipboard(text, label, obscure, verify):
		return
	logger.warning(f"Буферный ввод для {label} не подтвердился — fallback")
	# 2) posimvol fallback
	_type_with_logging(text, label, obscure)


def _get_current_layout_hex() -> str:
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = user32.GetForegroundWindow()
        thread_id = user32.GetWindowThreadProcessId(hwnd, 0)
        hkl = user32.GetKeyboardLayout(thread_id)
        lang = hkl & 0xFFFF
        return f"{lang:04x}"
    except Exception as exc:
        logger.warning(f"Не удалось получить раскладку: {exc}")
        return ""


def _ensure_english_layout(max_cycles: int = 6) -> None:
    target = "0409"  # EN-US
    current = _get_current_layout_hex()
    logger.info(f"Текущая раскладка: {current}")
    if current == target:
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hkl = user32.LoadKeyboardLayoutW("00000409", 1)
        user32.ActivateKeyboardLayout(hkl, 0)
        time.sleep(0.1)
    except Exception as exc:
        logger.warning(f"ActivateKeyboardLayout не сработал: {exc}")
    # Проверяем и, если надо, циклим Win+Space
    for i in range(max_cycles):
        current = _get_current_layout_hex()
        if current == target:
            logger.info("Раскладка EN-US активна")
            return
        logger.info("Переключаю раскладку Win+Space")
        pyautogui.hotkey("win", "space")
        time.sleep(0.2)
    logger.warning("Не удалось гарантированно переключить раскладку на EN-US")


def ensure_english_layout() -> None:
    """Public helper to ensure EN-US layout. Call before any typing."""
    _ensure_english_layout()


def _first_existing(paths: list[Path]) -> Optional[Path]:
    for p in paths:
        if p.exists():
            return p
    # try common variants
    folder = Path("dataset/templates")
    for cand in sorted(folder.glob("krestik*.png")):
        return cand
    return None


def _read_accounts(accounts_path: Path) -> Iterable[Credentials]:
    lines = accounts_path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip("\ufeff\n\r ")  # strip BOM and whitespace
        if not line:
            continue
        if "\t" in line:
            parts = line.split("\t")
        else:
            parts = line.split()
        if len(parts) < 2:
            logger.warning(f"Пропуск строки accounts.txt (ожидалось email\tpassword): {line}")
            continue
        email, password = parts[0].strip(), parts[1].strip()
        yield Credentials(email=email, password=password)


def _get_launcher_rect() -> Tuple[int, int, int, int] | None:
    import win32gui
    cfg = load_launcher_config()
    hwnd = find_launcher_hwnd_by_titles(cfg.window_title_patterns)
    if not hwnd:
        return None
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return left, top, right - left, bottom - top


def _get_launcher_hwnd() -> Optional[int]:
	cfg = load_launcher_config()
	hwnd = find_launcher_hwnd_by_titles(cfg.window_title_patterns)
	return hwnd


def _uia_fill_login_fields(creds: Credentials) -> bool:
	"""Try to set email/password using UIAutomation ValuePattern."""
	try:
		hwnd = _get_launcher_hwnd()
		if not hwnd:
			return False
		wnd = auto.ControlFromHandle(hwnd)
		wnd.SetFocus()
		# Find first two Edit controls in the dialog
		email_edit = wnd.EditControl(searchDepth=25, foundIndex=1)
		pwd_edit = wnd.EditControl(searchDepth=25, foundIndex=2)
		ok = False
		if email_edit and email_edit.Exists(1):
			logger.info("UIA: нашёл поле email через UIA")
			try:
				email_edit.SetFocus()
				email_edit.GetValuePattern().SetValue(creds.email)
				ok = True
			except Exception:
				email_edit.Click()
				auto.SendKeys('{Ctrl}a{Del}')
				auto.SendKeys(creds.email)
				ok = True
		if pwd_edit and pwd_edit.Exists(1):
			logger.info("UIA: нашёл поле password через UIA")
			try:
				pwd_edit.SetFocus()
				pwd_edit.GetValuePattern().SetValue(creds.password)
			except Exception:
				pwd_edit.Click()
				auto.SendKeys('{Ctrl}a{Del}')
				auto.SendKeys(creds.password)
				
		return ok
	except Exception as exc:
		logger.debug(f"UIA ввод не удался: {exc}")
		return False


def _grab_launcher_image() -> Image.Image | None:
    rect = _get_launcher_rect()
    if rect is None:
        return None
    l, t, w, h = rect
    shot = pyautogui.screenshot(region=(l, t, w, h))
    return shot


def _to_abs(rx: float, ry: float) -> Tuple[int, int] | None:
    rect = _get_launcher_rect()
    if rect is None:
        return None
    l, t, w, h = rect
    x = int(l + rx * w)
    y = int(t + ry * h)
    return x, y


def _hover_avatar_and_scroll(amount: int, dx_px: int = AVATAR_SCROLL_DX) -> None:
    pos = _to_abs(*AVATAR_RXY)
    if pos is None:
        return
    x, y = pos
    target = (x + dx_px, y)
    pyautogui.moveTo(*target)
    time.sleep(0.1)
    pyautogui.scroll(amount)
    logger.info(f"Scrolled from avatar at {target} amount={amount}")
    time.sleep(STEP_DELAY)


def _click_relative(rx: float, ry: float) -> None:
    pos = _to_abs(rx, ry)
    if pos is None:
        return
    x, y = pos
    pyautogui.click(x, y)
    time.sleep(STEP_DELAY)


def _hover_and_scroll(rx: float, ry: float, amount: int, dx_px: int = HOVER_DX) -> None:
    pos = _to_abs(rx, ry)
    if pos is None:
        return
    x, y = pos
    pyautogui.moveTo(x + dx_px, y)
    time.sleep(0.1)
    pyautogui.scroll(amount)
    logger.info(f"Hovered at {(x + dx_px, y)} and scrolled amount={amount}")
    time.sleep(STEP_DELAY)


def _find_template(path: Path, panel: str = "any") -> Optional[Tuple[int, int]]:
    if not path.exists():
        return None
    for conf in (0.86, 0.84, 0.80):
        pos = locate_template_on_launcher(path, panel=panel, confidence=conf)
        if pos:
            return pos
    for conf in (0.84, 0.80, 0.75):
        pos = locate_template_on_launcher(path, panel=panel, confidence=conf, grayscale=True)
        if pos:
            return pos
    return None


def login_once(dataset_root: Path, creds: Credentials) -> bool:
    if not ensure_launcher_visible():
        logger.error("Лаунчер не удалось показать")
        return False

    clf = PHashStateClassifier(dataset_root)
    templates = clf.load()
    if templates == 0:
        logger.error("Нет шаблонов для классификации состояний")
        return False

    # 1) ждём главное меню
    saw_main = False
    for _ in range(10):
        img = _grab_launcher_image()
        if img is None:
            time.sleep(0.2)
            continue
        match = clf.classify(img)
        if match and match.state == "main_menu":
            logger.info(f"Состояние: main_menu (dist={match.distance})")
            saw_main = True
            break
        time.sleep(0.2)
    if not saw_main:
        logger.warning("Главное меню лаунчера не найдено — пробую клик по аватару и повторную проверку")
        try:
            _click_relative(*AVATAR_RXY)
            time.sleep(1.0)
        except Exception:
            pass
        for _ in range(8):
            img = _grab_launcher_image()
            if img is None:
                time.sleep(0.2)
                continue
            match = clf.classify(img)
            if match and match.state == "main_menu":
                logger.info("Главное меню найдено после клика по аватару")
                saw_main = True
                break
            time.sleep(0.2)
        if not saw_main:
            logger.error("Главное меню лаунчера не найдено — перезапуск входа")
            return False

    # 2) нажать аватар
    _click_relative(*AVATAR_RXY)
    time.sleep(1.0)

    # 3) цикл состояний до формы логина
    made_progress = False  # отметим любое действие: logout/add-account/login
    for _ in range(90):
        img = _grab_launcher_image()
        if img is None:
            time.sleep(0.2)
            continue
        match = clf.classify(img)
        if not match:
            time.sleep(0.2)
            continue
        logger.info(f"Опознано состояние: {match.state} (dist={match.distance})")

        if match.state == "login_menu":
            # First try robust UIA-based input
            if _uia_fill_login_fields(creds):
                logger.info("UIA ввод выполнен, нажимаю 'Войти'")
            else:
                # Use templates for email/password fields and login button with clipboard fallback
                email_pos = _find_template(EMAIL_TEMPLATE, panel="any")
                if email_pos:
                    logger.info(f"Поле email найдено на {email_pos}")
                    pyautogui.click(*email_pos)
                    time.sleep(STEP_DELAY)
                    _input_text_smart(creds.email, label="email", obscure=False)
                else:
                    logger.warning("Шаблон email.png не найден — кликаю по относительной точке")
                    _click_relative(0.40, 0.28)
                    _input_text_smart(creds.email, label="email", obscure=False)

                password_pos = _find_template(PASSWORD_TEMPLATE, panel="any")
                if password_pos:
                    logger.info(f"Поле password найдено на {password_pos}")
                    pyautogui.click(*password_pos)
                    time.sleep(STEP_DELAY)
                    _input_text_smart(creds.password, label="password", obscure=True)
                else:
                    logger.warning("Шаблон password.png не найден — кликаю по относительной точке")
                    _click_relative(0.40, 0.36)
                    _input_text_smart(creds.password, label="password", obscure=True)

            login_btn_pos = _find_template(LOGIN_BTN_TEMPLATE, panel="any")
            if login_btn_pos:
                logger.info(f"Кнопка 'Войти' найдена на {login_btn_pos}")
                pyautogui.click(*login_btn_pos)
                time.sleep(STEP_DELAY)
            else:
                logger.warning("Шаблон login_btn.png не найден — кликаю по относительной точке")
                _click_relative(0.55, 0.44)
                time.sleep(STEP_DELAY)

            # Быстрая проверка ошибки логина (неверные данные)
            if LOGIN_ERROR_TEMPLATE.exists():
                for _ in range(10):
                    pos_err = locate_template_on_launcher(LOGIN_ERROR_TEMPLATE, panel="any", confidence=0.86)
                    if not pos_err:
                        pos_err = locate_template_on_launcher(LOGIN_ERROR_TEMPLATE, panel="any", confidence=0.80, grayscale=True)
                    if pos_err:
                        logger.error("Ошибка логина: обнаружен индикатор login_error.png — пропускаю аккаунт")
                        raise LoginInvalidError("Invalid credentials detected by login_error.png")
                    time.sleep(0.2)
            made_progress = True
            break

        if match.state == "account_is_login":
            found = False
            if LOGOUT_TEMPLATE.exists():
                for i in range(MAX_SCROLL_TRIES):
                    pos = locate_template_on_launcher(LOGOUT_TEMPLATE, panel="left", confidence=0.84)
                    if pos:
                        logger.info(f"Шаблон 'Выйти' найден на {pos} (попытка {i+1}/{MAX_SCROLL_TRIES})")
                        pyautogui.click(*pos)
                        time.sleep(STEP_DELAY)
                        found = True
                        made_progress = True
                        break
                    else:
                        logger.info(f"Шаблон 'Выйти' не найден (попытка {i+1}/{MAX_SCROLL_TRIES}) — скроллю от аватарки")
                        _hover_avatar_and_scroll(LOGOUT_SCROLL_AMOUNT)
            else:
                logger.warning("Нет шаблона logout.png — невозможно продолжить")
            if found:
                continue
            else:
                logger.error("Не удалось найти кнопку 'Выйти' после скроллов")
                return False

        if match.state == "account_logout":
            krestik_path = _first_existing([
                KRESTIK_TEMPLATE,
                Path("dataset/templates/krestik2.png"),
                Path("dataset/templates/kresik.png"),
            ])
            if krestik_path:
                pos = locate_template_on_launcher(krestik_path, panel="left", confidence=0.80)
                if not pos:
                    pos = locate_template_on_launcher(krestik_path, panel="left", confidence=0.78)
                if pos:
                    logger.info(f"Крестик найден в левой панели ({krestik_path.name}) на {pos}")
                    pyautogui.click(*pos)
                    time.sleep(STEP_DELAY)
                    made_progress = True
                else:
                    logger.error(f"Крестик не найден в левой панели по шаблону {krestik_path.name}")
                    return False
            else:
                logger.error("Файл шаблона крестика не найден (krestik*.png)")
                return False
            continue

        if match.state == "account_logout_conf":
            if CONTINUE_TEMPLATE.exists():
                pos = locate_template_on_launcher(CONTINUE_TEMPLATE, panel="left", confidence=0.84)
                if not pos:
                    pos = locate_template_on_launcher(CONTINUE_TEMPLATE, panel="any", confidence=0.80)
                if not pos:
                    pos = locate_template_on_launcher(CONTINUE_TEMPLATE, panel="any", confidence=0.75, grayscale=True)
                if pos:
                    logger.info(f"Кнопка 'Продолжить' найдена на {pos}")
                    # Одиночный клик без удержания
                    x, y = pos
                    pyautogui.click(x, y)
                    time.sleep(STEP_DELAY)
                else:
                    logger.warning("Шаблон continue.png не найден — кликаю по относительной точке")
                    _click_relative(*LOGOUT_CONF_CONTINUE_RXY)
            else:
                _click_relative(*LOGOUT_CONF_CONTINUE_RXY)
            _click_relative(*ADD_ACCOUNT_RXY)
            made_progress = True
            continue

        if match.state == "main_menu":
            _click_relative(*AVATAR_RXY)
            time.sleep(1.0)
            continue

        time.sleep(0.2)

    # Если не было прогресса (не логинились/не разлогинивались) — считаем неуспех, чтобы не скипать аккаунт
    if not made_progress:
        logger.warning("Прогресса нет после клика по аватару — не запускаю игру для предотвращения скипа аккаунта")
        return False

    # 4) дождаться главной после входа
    for _ in range(20):
        img = _grab_launcher_image()
        if img is None:
            time.sleep(0.2)
            continue
        match = clf.classify(img)
        if match and match.state == "main_menu":
            logger.info("Вернулись на главную")
            break
        time.sleep(0.15)

    # 5) нажать играть (наведение + 1с задержка перед кликом)
    pos = _to_abs(*PLAY_BTN_RXY)
    if pos is not None:
        x, y = pos
        pyautogui.moveTo(x, y)
        time.sleep(1.0)
        pyautogui.click(x, y)
        time.sleep(STEP_DELAY)
    else:
        _click_relative(*PLAY_BTN_RXY)
    # 6) убедиться, что окно игры действительно появилось
    try:
        from wotbot.config import load_game_config
        from wotbot.win.window_finder import find_game_hwnd_by_titles
        cfg_g = load_game_config()
        t0 = time.time()
        while time.time() - t0 < 30.0:
            hwnd_game = find_game_hwnd_by_titles(cfg_g.window_title_patterns)
            if hwnd_game:
                logger.info("Окно игры обнаружено после клика 'Играть'")
                return True
            time.sleep(0.5)
        logger.error("Игра не запустилась: окно клиента не найдено в отведённое время")
        raise GameStartTimeoutError("Game window did not appear in time")
    except Exception as exc:
        logger.warning(f"Проверка окна игры не удалась: {exc}")
        # В сомнительном состоянии лучше вернуть False, чтобы перезапустить попытку
        return False


def run_for_all_accounts(dataset_root: Path, accounts_path: Path) -> None:
    for creds in _read_accounts(accounts_path):
        try:
            ok = login_once(dataset_root, creds)
            if ok:
                logger.info(f"Логин успешен: {creds.email}")
            else:
                logger.warning(f"Логин не удался: {creds.email}")
        except Exception as exc:
            logger.exception(f"Ошибка при обработке {creds.email}: {exc}")
