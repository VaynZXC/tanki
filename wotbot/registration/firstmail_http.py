from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Iterable

import requests
from loguru import logger

from .firstmail_keys import FIRSTMAIL_API_KEY


@dataclass(frozen=True)
class BoughtMailbox:
    email: str
    password: str
    raw: dict[str, Any]


class FirstmailHttpClient:
    """Thin HTTP client for Firstmail Market API.

    Примечание: эндпоинты и параметры могут отличаться. При необходимости обновите URL/поля.
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None, key_file: str | None = None, proxy_url: str | None = None) -> None:
        self.api_key = api_key or os.getenv("FIRSTMAIL_API_KEY") or self._read_key_file(key_file) or FIRSTMAIL_API_KEY
        # v1.4.1 base URL per docs
        self.base_url = base_url or "https://api.firstmail.ltd/v1"
        # Avoid system proxies for Firstmail requests
        self._session = requests.Session()
        try:
            self._session.trust_env = False
        except Exception:
            pass
        # optional authenticated proxy for Firstmail traffic
        if proxy_url:
            parsed = self._build_proxy_url(proxy_url)
            if parsed:
                self._session.proxies = {"http": parsed, "https": parsed}

    def _read_key_file(self, key_file: str | None) -> str | None:
        candidates: list[Path] = []
        if key_file:
            candidates.append(Path(key_file))
        # CWD
        candidates.append(Path.cwd() / "firstmail_key.txt")
        # Project root (two levels up from this file)
        candidates.append(Path(__file__).resolve().parents[2] / "firstmail_key.txt")
        for p in candidates:
            try:
                if p.exists():
                    value = p.read_text(encoding="utf-8").strip()
                    if value:
                        return value
            except Exception:
                continue
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-KEY": self.api_key,
            "Accept": "application/json",
            "User-Agent": "WoTScript/1.0 (+https://local)",
            "Connection": "keep-alive",
        }

    @staticmethod
    def _build_proxy_url(raw: str) -> str | None:
        try:
            host, port, user, pwd = raw.split(":", 3)
            scheme = "http"
            return f"{scheme}://{user}:{pwd}@{host}:{port}"
        except Exception:
            return None

    def get_last_unread_message(self, email: str, username: str | None = None, password: str | None = None) -> dict[str, Any]:
        """GET /market/get/message for specified email (Market API).

        Некоторые ключи требуют username/password (учётка почты с маркета).
        """
        paths = [
            "/market/get/message",          # Market API (если куплена на маркете)
            "/imap/get/message",            # IMAP API (вариант 1)
            "/imap/get/message/one",        # IMAP API (вариант 2)
            "/imap/message/one",            # IMAP API (вариант 3)
            "/imap/messages/last",          # IMAP API (вариант 4)
        ]
        # Перебираем возможные комбинации параметров: username/login + optional domain/email
        local_part = None
        domain_part = None
        if email and "@" in email:
            local_part, domain_part = email.split("@", 1)

        headers = self._headers()

        last_error: str | None = None
        content_keys = ("html", "message", "text", "body", "subject")
        def _has_content(d: dict[str, Any] | None) -> bool:
            if not isinstance(d, dict):
                return False
            for k in content_keys:
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return True
            return False
        for path in paths:
            url = f"{self.base_url.rstrip('/')}{path}"
            # Собираем кандидаты параметров отдельно для каждого пути
            is_market = "/market/" in path
            param_candidates: list[dict[str, Any]] = []
            if is_market:
                # Для market API не используем параметр email: требуется username
                if email:
                    d = {"username": email}
                    if password:
                        d["password"] = password
                    param_candidates.append(d)
                if local_part and domain_part:
                    for ukey in ("username", "login"):
                        d = {ukey: local_part, "domain": domain_part}
                        if password:
                            d["password"] = password
                        param_candidates.append(d)
                if local_part:
                    d = {"username": local_part}
                    if password:
                        d["password"] = password
                    param_candidates.append(d)
            else:
                # Для imap API сначала пробуем email (+password), затем username=email
                if email:
                    d = {"email": email}
                    if password:
                        d["password"] = password
                    param_candidates.append(d)
                if email:
                    d = {"username": email}
                    if password:
                        d["password"] = password
                    param_candidates.append(d)
                if local_part and domain_part:
                    for ukey in ("username", "login"):
                        d = {ukey: local_part, "domain": domain_part}
                        if password:
                            d["password"] = password
                        param_candidates.append(d)
                if local_part:
                    d = {"username": local_part}
                    if password:
                        d["password"] = password
                    param_candidates.append(d)

            for params in param_candidates:
                safe_params = dict(params)
                if "password" in safe_params:
                    safe_params["password"] = "***"
                logger.info(f"GET {url} params={safe_params}")
                for attempt in range(1, 3):
                    try:
                        resp = self._session.get(
                            url,
                            headers=headers,
                            params=params,
                            timeout=(10, 15),
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, dict):
                                # Явный флаг наличия сообщения
                                if data.get("has_message") is True:
                                    return data
                                # Контент на корневом уровне
                                if _has_content(data):
                                    return data
                                # Сообщение во вложенных ключах
                                nested = data.get("data") or data.get("result") or data.get("payload") or None
                                if _has_content(nested if isinstance(nested, dict) else None):
                                    return data
                                # Иногда приходит массив messages/items
                                msgs = data.get("messages") or data.get("items")
                                if isinstance(msgs, list) and any(_has_content(m) for m in msgs if isinstance(m, dict)):
                                    return data
                            # Если дошли сюда — полезного содержимого нет, пробуем другие параметры/пути
                            last_error = last_error or "Empty/unknown payload"
                        else:
                            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                            logger.warning(f"Attempt {attempt} failed: {last_error}")
                    except Exception as exc:
                        last_error = f"{type(exc).__name__}: {exc}"
                        logger.warning(f"Attempt {attempt} exception: {last_error}")
                    try:
                        import time as _t
                        _t.sleep(0.8 * attempt)
                    except Exception:
                        pass
        raise RuntimeError(last_error or "All attempts failed")

    def get_last_message_imap(self, email: str, username: str | None = None, password: str | None = None) -> dict[str, Any]:
        """Force IMAP API to fetch the last message (even if already read)."""
        paths = [
            "/imap/get/message",
            "/imap/message/one",
            "/imap/get/message/one",
            "/imap/messages/last",
            "/imap/message/last",
            "/imap/get/last",
        ]
        # попробуем разные комбинации параметров, так как доки для IMAP отличаются
        param_candidates: list[dict[str, Any]] = []
        if username and password:
            param_candidates.append({"username": username, "password": password})
        if username:
            param_candidates.append({"username": username})
        # иногда требуют email
        param_candidates.append({"email": email})
        if username and password:
            param_candidates.append({"email": email, "username": username, "password": password})
        headers = self._headers()
        last_error: str | None = None
        for path in paths:
            url = f"{self.base_url.rstrip('/')}{path}"
            for params in param_candidates:
                safe_params = dict(params)
                if "password" in safe_params:
                    safe_params["password"] = "***"
                logger.info(f"GET {url} params={safe_params}")
                try:
                    resp = self._session.get(url, headers=headers, params=params, timeout=(10, 20))
                    if resp.status_code == 200:
                        return resp.json()
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
        raise RuntimeError(last_error or "IMAP fetch failed")

    def get_last_message_any(self, email: str, username: str | None = None, password: str | None = None) -> dict[str, Any]:
        """Try unread (Market) first, then various IMAP combinations for read messages."""
        try:
            return self.get_last_unread_message(email=email, username=username, password=password)
        except Exception:
            pass
        return self.get_last_message_imap(email=email, username=username, password=password)

    def _try_buy(self, url: str, auth_mode: str, mailbox_type: int, method: str, payload: str) -> BoughtMailbox | None:
        # auth_mode: 'x-api-key' | 'bearer' | 'authorization' | 'x-token' | 'x-key' | 'api-key' | 'query'
        headers: dict[str, str] = {"Accept": "application/json"}
        params: dict[str, Any] = {"type": mailbox_type}
        if auth_mode == "x-api-key":
            headers["X-API-KEY"] = self.api_key
        elif auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif auth_mode == "authorization":
            headers["Authorization"] = self.api_key
        elif auth_mode == "x-token":
            headers["X-Token"] = self.api_key
        elif auth_mode == "x-key":
            headers["X-Key"] = self.api_key
        elif auth_mode == "api-key":
            headers["Api-Key"] = self.api_key
        elif auth_mode == "query":
            params["api_key"] = self.api_key

        req_kwargs: dict[str, Any] = {"headers": headers, "timeout": 30}
        if payload == "params":
            req_kwargs["params"] = params
        elif payload == "json":
            req_kwargs["json"] = params
        elif payload == "data":
            req_kwargs["data"] = params

        logger.info(f"{method} {url} mode={auth_mode} payload={payload} params={params}")
        # use a short-lived request without session proxies for market buy (safer to use session too)
        resp = self._session.request(method=method, url=url, **req_kwargs)
        if resp.status_code != 200:
            logger.warning(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        # v1.4.1 may return {'email':..., 'password':...} or {'login': 'email:password'}
        email = data.get("email") or data.get("mail")
        password = data.get("password") or data.get("pass")
        login_field = data.get("login") or data.get("Login") or data.get("user")
        if isinstance(login_field, str):
            if ":" in login_field and not (email and password):
                e, p = login_field.split(":", 1)
                email = email or e
                password = password or p
            elif "|" in login_field and not (email and password):
                e, p = login_field.split("|", 1)
                email = email or e
                password = password or p
        if email and password:
            return BoughtMailbox(email=email, password=password, raw=data)
        status = (data.get("status") or data.get("ok") or False)
        if status in (True, "success", "ok"):
            raise RuntimeError(f"Status OK but no credentials: {json.dumps(data)[:300]}")
        logger.warning(f"Unexpected response: {data}")
        return None

    def buy_mailbox(self, mailbox_type: int = 3, paths: Iterable[str] | None = None, auth_modes: Iterable[str] | None = None, methods: Iterable[str] | None = None, payloads: Iterable[str] | None = None) -> BoughtMailbox:
        """Buy mailbox on Firstmail market.

        mailbox_type: 3 - как пример ("вечная" почта). Уточните по документации.
        """
        # Docs: GET https://api.firstmail.ltd/v1/market/buy/mail  (но пример показывает POST на старом хосте)
        paths = list(paths or ("/market/buy/mail", "/lk/get/email"))
        auth_modes = list(auth_modes or ("x-api-key",))
        methods = list(methods or ("GET", "POST"))
        payloads = list(payloads or ("params",))
        last_error: str | None = None
        for path in paths:
            for mode in auth_modes:
                for method in methods:
                    for payload in payloads:
                        url = f"{self.base_url.rstrip('/')}{path}"
                        try:
                            result = self._try_buy(url=url, auth_mode=mode, mailbox_type=mailbox_type, method=method, payload=payload)
                            if result is not None:
                                return result
                        except Exception as exc:
                            last_error = f"{type(exc).__name__}: {exc}"
                            logger.warning(f"Attempt failed: {last_error}")
        raise RuntimeError(last_error or "All attempts failed")


