from __future__ import annotations

import time
from typing import List, Optional

from loguru import logger


try:
    from interception import ffi, lib  # type: ignore
    _HAS_INTERCEPTION = True
except Exception as exc:  # pragma: no cover - optional runtime path
    logger.debug(f"Interception import failed: {exc}")
    _HAS_INTERCEPTION = False
    ffi = None  # type: ignore
    lib = None  # type: ignore


# Common scan codes we need in the project (set here to avoid duplication)
SC_ENTER = 0x1C
SC_ESCAPE = 0x01
SC_SPACE = 0x39


class DriverKeyboard:
    """Thin wrapper over Interception to send keyboard scancodes via driver.

    - Enumerates keyboard devices 1..20
    - Sends key down/up (with optional E0 flag) to all detected keyboards
    """

    def __init__(self) -> None:
        if not _HAS_INTERCEPTION:
            raise RuntimeError("interception library is not available")

        self.ctx = lib.interception_create_context()
        if not self.ctx:
            raise RuntimeError("interception_create_context failed")

        # Set a permissive filter (mostly required for receive, harmless for send)
        try:
            lib.interception_set_filter(
                self.ctx,
                lib.interception_is_keyboard,
                lib.INTERCEPTION_FILTER_KEY_ALL,
            )
        except Exception:
            # Some builds may not expose filters; sending still works
            pass

        self.keyboard_devices: List[int] = []
        for dev in range(1, 21):
            try:
                if lib.interception_is_keyboard(dev):
                    self.keyboard_devices.append(dev)
            except Exception:
                # Ignore out-of-range indices gracefully
                continue

        logger.info(
            f"Driver keyboard initialized: {len(self.keyboard_devices)} device(s)"
        )

    @property
    def num_keyboards(self) -> int:
        return len(self.keyboard_devices)

    def _send_scan_once(self, device: int, scan_code: int, e0: bool) -> None:
        key = ffi.new("InterceptionKeyStroke *")
        key.code = scan_code
        # state is DOWN or UP; E0 flag OR'ed if needed
        key.state = lib.INTERCEPTION_KEY_DOWN | (lib.INTERCEPTION_KEY_E0 if e0 else 0)
        key.information = 0
        stroke = ffi.cast("InterceptionStroke *", key)
        lib.interception_send(self.ctx, device, stroke, 1)
        # small spacing between down/up
        time.sleep(0.01)

        key.state = lib.INTERCEPTION_KEY_UP | (lib.INTERCEPTION_KEY_E0 if e0 else 0)
        lib.interception_send(self.ctx, device, stroke, 1)

    def send_scan(self, scan_code: int, repeats: int = 1, e0: bool = False) -> int:
        if self.num_keyboards == 0:
            return 0
        sent = 0
        for _ in range(max(1, repeats)):
            for dev in self.keyboard_devices:
                try:
                    self._send_scan_once(dev, scan_code, e0)
                    sent += 1
                except Exception as exc:
                    logger.debug(f"Driver send failed on device {dev}: {exc}")
            # small delay between repeats to mimic human input
            time.sleep(0.02)
        return sent

    def close(self) -> None:
        try:
            lib.interception_destroy_context(self.ctx)
        except Exception:
            pass


_driver: Optional[DriverKeyboard] = None


def init_driver() -> Optional[DriverKeyboard]:
    global _driver
    if _driver is not None:
        return _driver
    if not _HAS_INTERCEPTION:
        logger.info("Driver mode: interception module not available")
        return None
    try:
        _driver = DriverKeyboard()
        logger.info(
            f"Driver mode enabled (keyboards: {_driver.num_keyboards})"
        )
        return _driver
    except Exception as exc:
        logger.info(f"Driver mode unavailable: {exc}")
        return None


def driver_is_available() -> bool:
    return _driver is not None and _driver.num_keyboards > 0


def driver_press_scan(scan_code: int, repeats: int = 1, e0: bool = False) -> bool:
    if not driver_is_available():
        return False
    assert _driver is not None
    sent = _driver.send_scan(scan_code, repeats=repeats, e0=e0)
    logger.debug(f"Driver press scan=0x{scan_code:02X} repeats={repeats} sent={sent}")
    return sent > 0


# Ephemeral one-shot press: create/destroy context only during send
def driver_press_scan_ephemeral(scan_code: int, repeats: int = 1, e0: bool = False) -> bool:
    if not _HAS_INTERCEPTION:
        return False
    try:
        ctx = lib.interception_create_context()
        if not ctx:
            return False
        # enumerate keyboards
        devices = []
        for dev in range(1, 21):
            try:
                if lib.interception_is_keyboard(dev):
                    devices.append(dev)
            except Exception:
                continue
        if not devices:
            lib.interception_destroy_context(ctx)
            return False
        key = ffi.new("InterceptionKeyStroke *")
        stroke = ffi.cast("InterceptionStroke *", key)
        sent = 0
        for _ in range(max(1, repeats)):
            for dev in devices:
                key.code = scan_code
                key.state = lib.INTERCEPTION_KEY_DOWN | (lib.INTERCEPTION_KEY_E0 if e0 else 0)
                key.information = 0
                lib.interception_send(ctx, dev, stroke, 1)
                time.sleep(0.005)
                key.state = lib.INTERCEPTION_KEY_UP | (lib.INTERCEPTION_KEY_E0 if e0 else 0)
                lib.interception_send(ctx, dev, stroke, 1)
                sent += 1
            time.sleep(0.01)
        lib.interception_destroy_context(ctx)
        logger.debug(f"Driver(ephemeral) scan=0x{scan_code:02X} repeats={repeats} sent={sent}")
        return sent > 0
    except Exception as exc:
        try:
            lib.interception_destroy_context(ctx)  # type: ignore[name-defined]
        except Exception:
            pass
        logger.debug(f"driver_press_scan_ephemeral error: {exc}")
        return False

