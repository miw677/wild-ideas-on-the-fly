"""
Web browser module — Chromium in kiosk mode.

The BMW NBT has no internet browser.  This module runs Chromium on the
Pi and outputs it to the NBT display via the HDMI→LVDS adapter.

Chromium is chosen because:
  - Ships with Raspberry Pi OS
  - Has a proper kiosk mode (--kiosk flag disables the chrome chrome)
  - Supports touch input remapping via --touch-events=enabled

Touch input
-----------
The NBT's 8.8" display has a resistive touch digitiser that speaks
USB HID.  When the HDMI adapter is connected the Pi receives touch
events through USB; evdev remaps them to X11/Wayland pointer events.

iDrive rotary scroll
--------------------
The CAN bus module translates iDrive rotation to XDG key events
(Up/Down arrow keys) so Chromium can scroll pages without touch.

Homepage / bookmarks
--------------------
Configured via config/apps.yaml.  Sensible defaults: maps, radio, etc.
"""

import asyncio
import logging
import os
import shutil
from typing import Any

from .base_app import BaseApp

log = logging.getLogger(__name__)

CHROMIUM_BIN = (
    shutil.which("chromium-browser")
    or shutil.which("chromium")
    or "/usr/bin/chromium-browser"
)

DEFAULT_HOMEPAGE = "https://maps.google.com"

# Chromium flags tuned for a headless-ish kiosk on a Pi 4
CHROMIUM_FLAGS: list[str] = [
    "--kiosk",
    "--noerrdialogs",
    "--disable-infobars",
    "--disable-session-crashed-bubble",
    "--disable-restore-session-state",
    "--disable-features=TranslateUI",
    "--overscroll-history-navigation=0",
    "--touch-events=enabled",
    "--no-first-run",
    "--password-store=basic",
    "--use-gl=egl",                  # GPU acceleration on Pi 4
    "--enable-features=VaapiVideoDecoder",
    "--check-for-update-interval=31536000",  # never auto-update in car
]


class BrowserApp(BaseApp):
    name = "browser"

    def __init__(
        self,
        display_env: dict,
        homepage: str = DEFAULT_HOMEPAGE,
        extra_flags: list[str] | None = None,
        profile_dir: str = "/tmp/bmw-browser-profile",
    ) -> None:
        super().__init__(display_env)
        self._homepage = homepage
        self._extra_flags = extra_flags or []
        self._profile_dir = profile_dir

    def _build_command(self) -> list[str]:
        return [
            CHROMIUM_BIN,
            *CHROMIUM_FLAGS,
            *self._extra_flags,
            f"--user-data-dir={self._profile_dir}",
            self._homepage,
        ]

    async def launch(self) -> None:
        env = {**os.environ, **self._env}
        log.info("Launching Chromium → %s", self._homepage)
        self._proc = await asyncio.create_subprocess_exec(
            *self._build_command(),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._log_stderr())

    async def terminate(self) -> None:
        await self._kill_proc()

    async def navigate(self, url: str) -> None:
        """Send a new URL to the already-running Chromium via the debug port."""
        # Chromium must be started with --remote-debugging-port=9222 for this to work
        import urllib.request, json, urllib.error
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:9222/json/list", timeout=2
            ) as resp:
                tabs = json.loads(resp.read())
            if tabs:
                tab_id = tabs[0]["id"]
                ws_url = tabs[0]["webSocketDebuggerUrl"]
                # Simplified: just open a new tab via the REST endpoint
                data = json.dumps({"url": url}).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:9222/json/new?{url}",
                    method="PUT",
                )
                urllib.request.urlopen(req, timeout=2)
        except (urllib.error.URLError, KeyError):
            log.debug("Remote debug not available; ignoring navigate(%s)", url)

    async def _log_stderr(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        async for line in self._proc.stderr:
            decoded = line.decode().rstrip()
            # Chromium is noisy; only surface actual errors
            if "ERROR" in decoded or "FATAL" in decoded:
                log.warning("[chromium] %s", decoded)


class BrowserInputAdapter:
    """
    Translates iDrive rotation events into keyboard scroll events
    injected into the X11 root window (requires xdotool).
    """

    XDOTOOL = shutil.which("xdotool") or "xdotool"

    def __init__(self, display: str = ":0") -> None:
        self._display = display

    async def scroll(self, ticks: int) -> None:
        """Positive ticks = scroll down, negative = scroll up."""
        if ticks == 0:
            return
        key = "Down" if ticks > 0 else "Up"
        for _ in range(abs(ticks)):
            proc = await asyncio.create_subprocess_exec(
                self.XDOTOOL, "key", "--clearmodifiers", key,
                env={**os.environ, "DISPLAY": self._display},
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
