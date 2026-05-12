"""
CarPlay integration via UxPlay (open-source AirPlay/CarPlay server).

How it works
------------
1. UxPlay runs on the Pi and announces itself as an AirPlay 2 receiver
   on the local Wi-Fi network created by the Pi's built-in WLAN adapter.
2. The iPhone connects wirelessly and streams the CarPlay UI over AirPlay.
3. UxPlay renders the stream to the framebuffer / Wayland surface.
4. The Pi HDMI output feeds into the NBT's CVBS/LVDS camera input
   through a small HDMI→CVBS or HDMI→LVDS converter (e.g. Lenkeng LKV351).

Hardware requirements
---------------------
- Raspberry Pi 4 (or 5) with built-in Wi-Fi
- hostapd + dnsmasq to create the "BMW_CarPlay" AP (handled by install.sh)
- UxPlay >= 1.67 (supports full CarPlay, not just AirPlay mirroring)
- HDMI → CVBS or HDMI → LVDS adapter
- NBT front-camera or rear-camera input enabled via coding (E-Sys / BimmerCode)

iPhone pairing
--------------
  Settings → General → CarPlay → Available Cars → BMW CarPlay
  (First use only; subsequent connections are automatic within range.)

References
----------
  https://github.com/FDH2/UxPlay
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from .base_app import BaseApp

log = logging.getLogger(__name__)

UXPLAY_BIN = shutil.which("uxplay") or "/usr/local/bin/uxplay"

# The name advertised over Bonjour/mDNS – shows up on the iPhone
DEFAULT_SERVICE_NAME = "BMW 328i"

# Resolution must match the NBT display: 1280×480 for NBT, 800×480 for CIC
NBT_RESOLUTION = (1280, 480)


class CarPlayApp(BaseApp):
    name = "carplay"

    def __init__(
        self,
        display_env: dict,
        service_name: str = DEFAULT_SERVICE_NAME,
        resolution: tuple[int, int] = NBT_RESOLUTION,
        fullscreen: bool = True,
        audio_sink: str = "default",
    ) -> None:
        super().__init__(display_env)
        self._service_name = service_name
        self._resolution = resolution
        self._fullscreen = fullscreen
        self._audio_sink = audio_sink

    def _build_command(self) -> list[str]:
        w, h = self._resolution
        cmd = [
            UXPLAY_BIN,
            "-n", self._service_name,
            "-s", f"{w}x{h}",
            "-vs", "xvimagesink",       # video sink; change to waylandsink for Wayland
            "-as", self._audio_sink,
            "-nc",                       # no clutter (use bare GStreamer window)
            "-fps", "60",
        ]
        if self._fullscreen:
            cmd += ["-fs"]
        return cmd

    async def launch(self) -> None:
        if not Path(UXPLAY_BIN).exists():
            log.error(
                "UxPlay binary not found at %s. Run scripts/install.sh first.", UXPLAY_BIN
            )
            return

        env = {**os.environ, **self._env}
        log.info("Launching UxPlay as '%s' at %dx%d", self._service_name, *self._resolution)
        self._proc = await asyncio.create_subprocess_exec(
            *self._build_command(),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._log_output())

    async def terminate(self) -> None:
        await self._kill_proc()

    async def _log_output(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        async for line in self._proc.stderr:
            log.debug("[uxplay] %s", line.decode().rstrip())


class CarPlayManager:
    """
    Wraps CarPlayApp and handles the Wi-Fi AP lifecycle so the Pi creates
    the "BMW_CarPlay" network only while CarPlay is the active app.
    """

    HOSTAPD_SERVICE = "hostapd"
    DNSMASQ_SERVICE = "dnsmasq"

    def __init__(self, app: CarPlayApp) -> None:
        self._app = app

    async def activate(self) -> None:
        await self._set_ap(True)
        await self._app.start()

    async def deactivate(self) -> None:
        await self._app.stop()
        await self._set_ap(False)

    async def _set_ap(self, enable: bool) -> None:
        action = "start" if enable else "stop"
        for svc in (self.HOSTAPD_SERVICE, self.DNSMASQ_SERVICE):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", action, svc,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            except FileNotFoundError:
                log.warning("systemctl not available; skipping AP %s for %s", action, svc)
