from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Pattern, Sequence

from dotenv import load_dotenv


@dataclass(frozen=True)
class LauncherConfig:
    """Configuration for detecting and activating the WoT launcher.

    - `window_title_patterns`: Regex patterns to match launcher window titles (multi-language).
    - `tray_icon_name_hints`: Substrings to match tray icon accessible names.
    - `max_tray_probe_icons`: Safety cap for iterating system tray icons.
    """
    window_title_patterns: Sequence[Pattern[str]]
    tray_icon_name_hints: Sequence[str]
    max_tray_probe_icons: int = 30


@dataclass(frozen=True)
class GameConfig:
    """Configuration for detecting the WoT game client window."""
    window_title_patterns: Sequence[Pattern[str]]


_DEF_TITLE_PATTERNS = [
    # Common titles for Wargaming Game Center / World of Tanks launcher
    re.compile(r"(wargaming.*game\s*center)", re.IGNORECASE),
    re.compile(r"(world\s*of\s*tanks).*(launcher|game\s*center)?", re.IGNORECASE),
    re.compile(r"(игровой\s*центр|ворлд\s*оф\s*танкс).*(лаунчер|центр)", re.IGNORECASE),
]

_DEF_TRAY_HINTS = [
    "wargaming",
    "game center",
    "wgc",
    "world of tanks",
]

# Game window title patterns (client)
_DEF_GAME_TITLE_PATTERNS = [
    re.compile(r"^world\s*of\s*tanks\b(?!.*game\s*center)", re.IGNORECASE),
    re.compile(r"\bWOT CLIENT\b", re.IGNORECASE),
    re.compile(r"\bWorldOfTanks\b", re.IGNORECASE),
]


def load_launcher_config() -> LauncherConfig:
    load_dotenv(override=False)

    # Allow overrides through env vars (comma-separated values)
    titles_env = os.getenv("WOT_LAUNCHER_TITLE_PATTERNS", "").strip()
    hints_env = os.getenv("WOT_LAUNCHER_TRAY_HINTS", "").strip()

    title_patterns: list[Pattern[str]]
    if titles_env:
        title_patterns = [re.compile(p.strip(), re.IGNORECASE) for p in titles_env.split(",") if p.strip()]
    else:
        title_patterns = list(_DEF_TITLE_PATTERNS)

    tray_hints: list[str]
    if hints_env:
        tray_hints = [h.strip().lower() for h in hints_env.split(",") if h.strip()]
    else:
        tray_hints = list(_DEF_TRAY_HINTS)

    return LauncherConfig(
        window_title_patterns=title_patterns,
        tray_icon_name_hints=tray_hints,
        max_tray_probe_icons=int(os.getenv("WOT_LAUNCHER_MAX_TRAY_ICONS", "30")),
    )


def load_game_config() -> GameConfig:
    load_dotenv(override=False)
    titles_env = os.getenv("WOT_GAME_TITLE_PATTERNS", "").strip()
    title_patterns: list[Pattern[str]]
    if titles_env:
        title_patterns = [re.compile(p.strip(), re.IGNORECASE) for p in titles_env.split(",") if p.strip()]
    else:
        title_patterns = list(_DEF_GAME_TITLE_PATTERNS)
    return GameConfig(window_title_patterns=title_patterns)
