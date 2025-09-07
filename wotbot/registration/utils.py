from __future__ import annotations

import random
import re
import string
from typing import Iterable


def generate_password(length: int = 12) -> str:
    letters = string.ascii_letters
    digits = string.digits
    symbols = "!@#$%^&*()_+-="
    all_chars = letters + digits + symbols
    pwd = [
        random.choice(string.ascii_lowercase),
        random.choice(string.ascii_uppercase),
        random.choice(digits),
        random.choice(symbols),
    ]
    pwd += [random.choice(all_chars) for _ in range(max(4, length) - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


def extract_first_url(text: str) -> str | None:
    m = re.search(r"https?://\S+", text or "")
    return m.group(0) if m else None


def find_url_in_bodies(bodies: Iterable[str | None], hints: Iterable[str] | None = None) -> str | None:
    hints = list(hints or [])
    for body in bodies:
        if not body:
            continue
        if hints and not any(h.lower() in body.lower() for h in hints):
            continue
        url = extract_first_url(body)
        if url:
            return url
    return None


