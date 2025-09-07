from __future__ import annotations

from typing import Iterable, Optional
import re

from loguru import logger

# throttle counters
_log_counter = 0
_LOG_EVERY = 4  # log once every N successful finds
from pywinauto import findwindows


def find_launcher_hwnd_by_titles(title_patterns: Iterable[re.Pattern[str]]) -> Optional[int]:
    """Return the first matching top-level window handle for the launcher by title regex.

    Tries UIA backend first, then Win32 as a fallback.
    """
    global _log_counter
    patterns = list(title_patterns)
    if not patterns:
        return None

    # pywinauto.findwindows is backend-agnostic here for title matching
    for pattern in patterns:
        try:
            hwnds = findwindows.find_windows(title_re=pattern, visible_only=True)
            if hwnds:
                _log_counter += 1
                if _log_counter % _LOG_EVERY == 1:
                    logger.debug(f"Found window(s) for pattern {pattern.pattern!r}: {hwnds}")
                return int(hwnds[0])
        except findwindows.ElementNotFoundError:
            continue
        except Exception as exc:
            logger.warning(f"find_windows error for {pattern.pattern!r}: {exc}")

    # Log 'not found' rarer as well
    _log_counter += 1
    if _log_counter % _LOG_EVERY == 1:
        logger.debug("Launcher window not found by title regex patterns")
    return None


def find_game_hwnd_by_titles(title_patterns: Iterable[re.Pattern[str]]) -> Optional[int]:
    """Finds the first visible top-level game client window handle by title regex."""
    return find_launcher_hwnd_by_titles(title_patterns)
