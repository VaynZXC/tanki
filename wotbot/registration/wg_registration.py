from __future__ import annotations

import time
import random
import string
from dataclasses import dataclass
from typing import Iterable

from loguru import logger
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeoutError
import requests

from .firstmail_http import FirstmailHttpClient
from .utils import extract_first_url
import re
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class RegistrationResult:
    email: str
    password: str
    ok: bool
    error: str | None = None


def _gen_name_from_email(email: str, extra_len: int = 4) -> str:
    name = email.split("@", 1)[0]
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(extra_len))
    return (name[:10] + suffix).lower() + "2025"


def _fill_field(page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            try:
                loc.first.wait_for(state="visible", timeout=2500)
            except Exception:
                pass
            try:
                loc.first.click(timeout=800)
            except Exception:
                pass
            try:
                loc.first.fill(value)
                # logger.debug(f"[reg] Filled via locator: {sel}")
                return True
            except Exception:
                try:
                    page.evaluate("(args)=>{const [sel,val]=args; const el=document.querySelector(sel); if(el){el.value=val; el.dispatchEvent(new Event('input',{bubbles:true}));}}", [sel, value])
                    # logger.debug(f"[reg] Filled via JS: {sel}")
                    return True
                except Exception:
                    logger.debug(f"[reg] Fill failed for {sel}")
                    continue
        except Exception:
            continue
    return False


def _fill_passwords(page, password: str) -> int:
    """Заполняет оба поля пароля. Возвращает количество успешно заполненных полей (0..2)."""
    filled = 0
    # Try explicit IDs first
    if _fill_field(page, ["#password-regform"], password):
        filled += 1
    else:
        try:
            pwd_inputs = page.locator('input[type="password"]')
            if pwd_inputs.count() >= 1:
                pwd_inputs.nth(0).fill(password)
                filled += 1
        except Exception:
            pass

    # Confirm password
    if _fill_field(page, ["#password-confirm-regform"], password):
        filled += 1
    else:
        try:
            pwd_inputs = page.locator('input[type="password"]')
            if pwd_inputs.count() >= 2:
                pwd_inputs.nth(1).fill(password)
                filled += 1
        except Exception:
            pass
    return filled


def _open_in_same_context(page, url: str) -> bool:
    """Открывает ссылку в том же контексте: пробует текущую вкладку, затем новую вкладку.
    Возвращает True при успехе."""
    logger.info(f"[confirm] Navigating to confirmation URL in same context: {url}")
    try:
        page.goto(url)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PwTimeoutError:
            pass
        time.sleep(5.0)
        return True
    except Exception as exc:
        logger.debug(f"[confirm] page.goto failed: {exc}")
    # Fallback: JS навигация
    try:
        page.evaluate("(u)=>{window.location.href=u}", url)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PwTimeoutError:
            pass
        time.sleep(5.0)
        return True
    except Exception as exc:
        logger.debug(f"[confirm] JS location fallback failed: {exc}")
    # Fallback: новая вкладка в том же контексте
    try:
        new_page = page.context.new_page()
        new_page.goto(url)
        try:
            new_page.wait_for_load_state("networkidle", timeout=10000)
        except PwTimeoutError:
            pass
        time.sleep(5.0)
        return True
    except Exception as exc:
        logger.warning(f"[confirm] New page navigation failed: {exc}")
        return False

def _ensure_bonus_field_visible(page, attempts: int = 5, pause_after_click: float = 0.5) -> bool:
    """Открывает поле промокода, если оно скрыто. Возвращает True, если поле видно."""
    for _ in range(max(1, attempts)):
        try:
            if page.locator('#bonus-regform').is_visible():
                return True
        except Exception:
            pass
        for sel in [
            'label[for="bonus-regform"]',
            '#bonus-regform',
            '[data-testid="bonus-regform"]',
            'text=Pozvánkový kód',
            'text=Code d\'invitation',
            'text=Invitation Code',
        ]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    try:
                        loc.first.scroll_into_view_if_needed()
                    except Exception:
                        pass
                    loc.first.click()
                    time.sleep(pause_after_click)
                    break
            except Exception:
                continue
    try:
        return page.locator('#bonus-regform').is_visible()
    except Exception:
        return False

def _install_cookie_watcher_js(page, interval_ms: int = 5000) -> None:
    """Ставит JS-таймер в странице, который каждые interval_ms кликает по баннеру.
    Без потоков Python, безопасно для sync API.
    """
    script = (
        "(() => {\n"
        "  try {\n"
        "    if (window.__wotCookieWatcher) return;\n"
        "    const clickBanner = () => {\n"
        "      try {\n"
        "        const sels = ['#onetrust-accept-btn-handler', '#onetrust-banner-sdk #onetrust-accept-btn-handler'];\n"
        "        for (const s of sels) { const b = document.querySelector(s); if (b) { b.click(); return true; } }\n"
        "      } catch(e) {}\n"
        "      return false;\n"
        "    };\n"
        "    clickBanner();\n"
        f"    window.__wotCookieWatcher = setInterval(clickBanner, {interval_ms});\n"
        "  } catch(e) {}\n"
        "})();"
    )
    try:
        page.add_init_script(script)
    except Exception:
        pass
    try:
        page.evaluate(script)
    except Exception:
        pass

def _ensure_registration_form(page) -> None:
    """Убедиться, что форма регистрации открыта и поля видимы."""
    # logger.debug("[reg] Ensuring registration form is visible")
    for _ in range(3):
        try:
            email_present = page.locator('#email-regform').count() > 0
        except Exception:
            email_present = False
        if email_present:
            try:
                page.locator('#email-regform').first.wait_for(state='visible', timeout=2000)
            except Exception:
                pass
            return
        try:
            page.locator('#unknown-player-1_cta').click()
        except Exception:
            pass
        time.sleep(0.7)
    # финальная попытка дождаться любого email-поля
    try:
        page.locator('#email-regform, input[type="email"]').first.wait_for(state='visible', timeout=3000)
    except Exception:
        pass


def _accept_cookies(page, timeout_ms: int = 1500) -> bool:
    """Минимальная быстрая попытка закрыть OneTrust и сразу продолжить."""
    try:
        clicked = page.evaluate('() => {\n  const sels = [\n    "#onetrust-accept-btn-handler",\n    "#onetrust-banner-sdk #onetrust-accept-btn-handler"\n  ];\n  for (const s of sels) {\n    const b = document.querySelector(s);\n    if (b) { b.click(); return true; }\n  }\n  return false;\n}')
        if clicked:
            # logger.debug("[cookies] Accepted via JS")
            return True
    except Exception:
        pass
    try:
        btn = page.locator('id=onetrust-accept-btn-handler')
        if btn.count() > 0:
            btn.first.click(force=True, timeout=300)
            # logger.debug("[cookies] Accepted via locator")
            return True
    except Exception:
        pass
    return False

def register_on_site(
    email: str,
    password: str,
    name: str | None = None,
    url: str = "https://join.worldoftanks.eu/1613051096/",
    referral_code: str = "EPICWIN",
    birth_day: str = "01",
    birth_month: str = "01",
    birth_year: str = "1998",
    headless: bool = False,
    navigation_timeout_ms: int = 30000,
    proxy: dict | None = None,
) -> RegistrationResult:
    """Открывает страницу регистрации и заполняет форму по заданным id.

    Последовательность:
      - перейти на url, подождать 1с
      - клик по #unknown-player-1_cta
      - подождать 2с
      - заполнить поля: email-regform, password-regform, name-regform, password-confirm-regform, birthdate-*-regform
      - клик по label[for="bonus-regform"], ввести код в #bonus-regform
      - клик по #regform_submit
    """
    username = name or _gen_name_from_email(email)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless, proxy=proxy) if proxy else p.chromium.launch(headless=headless)
            # reduce console noise
            # if proxy:
            #     logger.debug(f"[reg] Using proxy at launch: {proxy.get('server', '')}")
            context = browser.new_context(proxy=proxy) if proxy else browser.new_context()
            page = context.new_page()
            # JS watcher для cookie-баннера (без потоков)
            _install_cookie_watcher_js(page, interval_ms=5000)
            page.set_default_timeout(navigation_timeout_ms)

            page.goto(url)
            # logger.debug("[reg] Page opened; trying to accept cookies ASAP")

            # Быстро закрыть куки (без долгих ретраев)
            try:
                _accept_cookies(page, timeout_ms=1500)
            except Exception:
                pass

            # logger.debug("[reg] Clicking 'unknown-player-1_cta'")
            page.locator("#unknown-player-1_cta").click()
            _ensure_registration_form(page)
            time.sleep(2.0)

            # email / username / passwords (robust fills) + progress
            total_fields = 5
            filled_count = 0
            if _fill_field(page, ["#email-regform", 'input[type="email"]'], email):
                filled_count += 1
            else:
                logger.warning("[reg] Email field not found by id; used generic selector")
            if _fill_field(page, ["#name-regform"], username):
                filled_count += 1
            else:
                logger.info("[reg] Name field not found by id; skipping if not required")
            filled_count += _fill_passwords(page, password)
            logger.info(f"[reg] Progress: filled {filled_count}/{total_fields} base fields")
            for sel, value in (
                ("#birthdate-day-regform", birth_day),
                ("#birthdate-month-regform", birth_month),
                ("#birthdate-year-regform", birth_year),
            ):
                try:
                    page.locator(sel).fill(value)
                except Exception:
                    try:
                        page.locator(sel).select_option(value, timeout=700)
                    except Exception:
                        pass
            filled_count += 1
            logger.info(f"[reg] Progress: filled {filled_count}/{total_fields} including birth date")
            # Ensure promo field is visible
            _ensure_bonus_field_visible(page, attempts=4, pause_after_click=0.5)
            try:
                bonus = page.locator('#bonus-regform')
                try:
                    bonus.wait_for(state='visible', timeout=2000)
                except Exception:
                    pass
                try:
                    bonus.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    bonus.click()
                    time.sleep(0.15)
                except Exception:
                    pass
                bonus.fill(referral_code, timeout=2000)
                logger.info(f"[reg] Progress: filled referral code ({filled_count}/{total_fields}+)")
            except Exception:
                try:
                    page.evaluate("(val)=>{const el=document.querySelector('#bonus-regform'); if(el){el.value=val; el.dispatchEvent(new Event('input',{bubbles:true}));}}", referral_code)
                except Exception:
                    pass
            lbl = page.locator('label[for="policy-regform"]').first
            try:
                lbl.scroll_into_view_if_needed()
            except Exception:
                pass
            lbl.click()
            time.sleep(0.1)
            if not page.locator("#policy-regform").is_checked():
                lbl.click(force=True)
                time.sleep(0.1)
            page.locator("#regform_submit").click()
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PwTimeoutError:
                pass
            # Ждём 10с перед проверкой почты, чтобы письмо гарантированно пришло
            time.sleep(10.0)

            # Подтверждение выполняется в отдельной функции, здесь только регистрация
            context.close()
            browser.close()
            return RegistrationResult(email=email, password=password, ok=True)
    except Exception as exc:
        return RegistrationResult(email=email, password=password, ok=False, error=str(exc))


# ===== Email confirmation helpers (Firstmail) =====
def _extract_url_candidates_from_response(resp: dict) -> list[str]:
    bodies: list[str] = []
    try:
        for key in ("html", "message", "text", "body"):
            v = resp.get(key)
            if isinstance(v, str) and v:
                bodies.append(v)
        # nested common wrappers
        for key in ("data", "result", "payload"):
            v = resp.get(key)
            if isinstance(v, dict):
                for kk in ("html", "message", "text", "body"):
                    vv = v.get(kk)
                    if isinstance(vv, str) and vv:
                        bodies.append(vv)
    except Exception:
        pass

    urls: list[str] = []
    for body in bodies:
        try:
            # try HTML first
            soup = BeautifulSoup(body, "html.parser")
            for a in soup.find_all("a"):
                href = a.get("href")
                if isinstance(href, str) and href.startswith("http"):
                    urls.append(href)
            # special-case: WG splits visible URL into <nobr> parts
            try:
                nobrs = soup.find_all("nobr")
                for nb in nobrs:
                    joined = "".join(list(nb.stripped_strings))
                    if isinstance(joined, str) and "eu.wargaming.net/registration/short" in joined:
                        candidate = joined.strip().strip('"\'')
                        if candidate.startswith("http"):
                            urls.append(candidate)
                        else:
                            # if protocol is missing (unlikely), default to https
                            urls.append("https://" + candidate.lstrip("/"))
            except Exception:
                pass
        except Exception:
            pass
        try:
            url_text = extract_first_url(body)
            if url_text:
                urls.append(url_text)
        except Exception:
            pass
    # prefer strict registration links first
    urls_registration = [u for u in urls if "/registration/" in u]
    urls_other = [u for u in urls if "/registration/" not in u]
    # among others, still prefer WG click-tracking
    prefer_other = ["tracking/click", "wargaming", "worldoftanks"]
    urls_other_sorted = sorted(urls_other, key=lambda u: (0 if any(p in u for p in prefer_other) else 1, len(u)))
    urls_sorted = urls_registration + urls_other_sorted
    # deduplicate preserving order
    seen = set()
    unique: list[str] = []
    for u in urls_sorted:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def _log_email_full_text(resp: dict) -> None:
    """Логирует максимально полный текст письма (subject + body)."""
    try:
        subject = None
        sender = None
        # Попытка достать метаданные
        for key in ("subject", "Subject"):
            subj = resp.get(key)
            if isinstance(subj, str) and subj.strip():
                subject = subj.strip()
                break
        for key in ("from", "sender", "Sender", "From"):
            s = resp.get(key)
            if isinstance(s, str) and s.strip():
                sender = s.strip()
                break
        if subject:
            logger.info(f"[confirm] Email subject: {subject}")
        if sender:
            logger.info(f"[confirm] Email from: {sender}")

        def html_to_text(raw: str) -> str:
            try:
                soup = BeautifulSoup(raw, "html.parser")
                return soup.get_text("\n", strip=False)
            except Exception:
                return raw

        bodies: list[str] = []
        # Основные поля
        for key in ("text", "message", "body"):
            v = resp.get(key)
            if isinstance(v, str) and v.strip():
                bodies.append(v)
        # HTML → text
        html = resp.get("html")
        if isinstance(html, str) and html.strip():
            bodies.append(html_to_text(html))
        # Вложенные структуры
        for key in ("data", "result", "payload"):
            v = resp.get(key)
            if isinstance(v, dict):
                for kk in ("text", "message", "body"):
                    vv = v.get(kk)
                    if isinstance(vv, str) and vv.strip():
                        bodies.append(vv)
                vv = v.get("html")
                if isinstance(vv, str) and vv.strip():
                    bodies.append(html_to_text(vv))
        # Коллекции сообщений (берём все)
        for key in ("messages", "items"):
            arr = resp.get(key)
            if isinstance(arr, list):
                for m in arr:
                    if not isinstance(m, dict):
                        continue
                    for kk in ("text", "message", "body"):
                        vv = m.get(kk)
                        if isinstance(vv, str) and vv.strip():
                            bodies.append(vv)
                    vv = m.get("html")
                    if isinstance(vv, str) and vv.strip():
                        bodies.append(html_to_text(vv))

        # Логируем все тела письма подряд
        if bodies:
            logger.info("[confirm] Email full text:")
            for idx, body in enumerate(bodies, start=1):
                logger.info(f"[confirm] ---- body {idx} begin ----")
                try:
                    logger.info(body)
                except Exception:
                    # На всякий случай печатаем repr
                    logger.info(repr(body))
                logger.info(f"[confirm] ---- body {idx} end ----")
    except Exception as exc:
        logger.debug(f"[confirm] Failed to log full email text: {exc}")


def fetch_confirmation_link_from_firstmail(email: str, mailbox_password: str | None = None, firstmail_proxy: str | None = None, unread_only: bool = False) -> str | None:
    client = FirstmailHttpClient(proxy_url=firstmail_proxy)
    # используем полный email как username согласно требованиям
    username = email
    try:
        if unread_only:
            resp = client.get_last_unread_message(email=email, username=username, password=mailbox_password)
        else:
            resp = client.get_last_message_any(email=email, username=username, password=mailbox_password)
    except Exception as exc:
        logger.warning(f"[confirm] Firstmail fetch error: {exc}")
        return None
    try:
        if isinstance(resp, dict):
            candidates = _extract_url_candidates_from_response(resp)
            for u in candidates:
                if "/registration/" in u:
                    return u
            return None
    except Exception:
        return None
    return None


def confirm_via_firstmail(email: str, timeout_sec: int = 180, headless: bool = False, mailbox_password: str | None = None, firstmail_proxy: str | None = None, page=None) -> bool:
    start = time.monotonic()
    deadline = start + max(1, timeout_sec)
    attempt = 0
    url: str | None = None
    # poll loop
    while True:
        now = time.monotonic()
        if now >= deadline:
            break
        attempt += 1
        unread_only = (now - start) < (timeout_sec / 2)
        url = fetch_confirmation_link_from_firstmail(email=email, mailbox_password=mailbox_password, firstmail_proxy=firstmail_proxy, unread_only=unread_only)
        if url:
            logger.info(f"[confirm] Link: {url}")
            break
        try:
            time.sleep(min(4.0, max(0.5, deadline - time.monotonic())))
        except Exception:
            pass
    if not url:
        logger.warning("[confirm] Confirmation link not found")
        return False
    # open in current page if provided, otherwise fresh minimal browser
    try:
        if page is not None:
            return _open_in_same_context(page, url)
        else:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context()
                pg = context.new_page()
                pg.goto(url)
                try:
                    pg.wait_for_load_state("networkidle", timeout=10000)
                except PwTimeoutError:
                    pass
                time.sleep(5.0)
                context.close()
                browser.close()
                return True
    except Exception as exc:
        logger.warning(f"[confirm] Navigation failed: {exc}")
        return False


def register_on_site_and_confirm_in_page(
    email: str,
    password: str,
    name: str | None = None,
    url: str = "https://join.worldoftanks.eu/1613051096/",
    referral_code: str = "EPICWIN",
    birth_day: str = "01",
    birth_month: str = "01",
    birth_year: str = "1998",
    headless: bool = False,
    navigation_timeout_ms: int = 30000,
    proxy: dict | None = None,
    mailbox_password: str | None = None,
    confirm_timeout_sec: int = 180,
    firstmail_proxy: str | None = None,
    confirm_once: bool = False,
) -> RegistrationResult:
    # 1+2) Register and confirm within same Playwright context (headful)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless, proxy=proxy) if proxy else p.chromium.launch(headless=headless)
            if proxy:
                logger.info(f"[reg] Using proxy at launch: {proxy.get('server', '')}")
            context = browser.new_context(proxy=proxy) if proxy else browser.new_context()
            page = context.new_page()
            _install_cookie_watcher_js(page, interval_ms=5000)
            page.set_default_timeout(navigation_timeout_ms)
            page.goto(url)
            logger.info("[reg] Page opened; trying to accept cookies ASAP")
            try:
                _accept_cookies(page, timeout_ms=1500)
            except Exception:
                pass
            logger.info("[reg] Clicking 'unknown-player-1_cta'")
            page.locator("#unknown-player-1_cta").click()
            _ensure_registration_form(page)
            time.sleep(2.0)

            total_fields = 5
            filled_count = 0
            if _fill_field(page, ["#email-regform", 'input[type="email"]'], email):
                filled_count += 1
            if _fill_field(page, ["#name-regform"], name or _gen_name_from_email(email)):
                filled_count += 1
            filled_count += _fill_passwords(page, password)
            for sel, value in (("#birthdate-day-regform", birth_day), ("#birthdate-month-regform", birth_month), ("#birthdate-year-regform", birth_year)):
                try:
                    page.locator(sel).fill(value)
                except Exception:
                    try:
                        page.locator(sel).select_option(value, timeout=700)
                    except Exception:
                        pass
            filled_count += 1
            _ensure_bonus_field_visible(page, attempts=4, pause_after_click=0.5)
            try:
                bonus = page.locator('#bonus-regform')
                try:
                    bonus.wait_for(state='visible', timeout=2000)
                except Exception:
                    pass
                try:
                    bonus.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    bonus.click()
                    time.sleep(0.15)
                except Exception:
                    pass
                bonus.fill(referral_code, timeout=2000)
            except Exception:
                try:
                    page.evaluate("(val)=>{const el=document.querySelector('#bonus-regform'); if(el){el.value=val; el.dispatchEvent(new Event('input',{bubbles:true}));}}", referral_code)
                except Exception:
                    pass
            lbl = page.locator('label[for="policy-regform"]').first
            try:
                lbl.scroll_into_view_if_needed()
            except Exception:
                pass
            lbl.click()
            time.sleep(0.1)
            if not page.locator("#policy-regform").is_checked():
                lbl.click(force=True)
                time.sleep(0.1)
            page.locator("#regform_submit").click()
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PwTimeoutError:
                pass
            time.sleep(10.0)

            ok = confirm_via_firstmail(email=email, timeout_sec=confirm_timeout_sec, headless=headless, mailbox_password=(mailbox_password or password), firstmail_proxy=firstmail_proxy, page=page)
            context.close()
            browser.close()
            return RegistrationResult(email=email, password=password, ok=ok)
    except Exception as exc:
        return RegistrationResult(email=email, password=password, ok=False, error=str(exc))
