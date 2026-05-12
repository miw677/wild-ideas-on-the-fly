"""
BMW NBT iDrive Extended — main entry point.

Run on boot via systemd (see scripts/bmw-nbt-extend.service).
Reads config from config/nbt.yaml, starts the CAN bus listener,
shows the launcher, and routes inputs to whichever app is active.

Quick start (development):
    python -m src.main --sim          # simulation mode, no hardware
    python -m src.main --app browser  # jump straight to browser
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import yaml

from .can_bus import BMWCanBus, IDriveButton, IDriveEvent, SteeringButton, SteeringEvent
from .apps import CarPlayApp, CarPlayManager, BrowserApp, BrowserInputAdapter, VideoPlayerApp
from .ui.launcher import Launcher

log = logging.getLogger(__name__)


def load_config(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


class NBTExtendApp:
    """
    Top-level application controller.

    State machine:
        LAUNCHER → (user selects app) → APP_ACTIVE → (back button) → LAUNCHER
    """

    APPS = ("carplay", "browser", "video")

    def __init__(self, config: dict, display_env: dict, sim: bool = False) -> None:
        self._cfg = config
        self._display_env = display_env
        self._sim = sim

        can_cfg = config.get("can_bus", {})
        self._can = BMWCanBus(
            channel=can_cfg.get("channel", "can0"),
            bitrate=can_cfg.get("bitrate", 100_000),
        )

        app_cfg = config.get("apps", {})
        self._carplay = CarPlayApp(
            display_env,
            service_name=app_cfg.get("carplay", {}).get("service_name", "BMW 328i"),
        )
        self._carplay_mgr = CarPlayManager(self._carplay)
        self._browser = BrowserApp(
            display_env,
            homepage=app_cfg.get("browser", {}).get("homepage", "https://maps.google.com"),
        )
        self._browser_adapter = BrowserInputAdapter(display_env.get("DISPLAY", ":0"))
        self._video = VideoPlayerApp(
            display_env,
            motion_pause=app_cfg.get("video", {}).get("motion_pause", True),
        )

        self._launcher = Launcher(on_launch=self._on_app_selected)
        self._active_app: Optional[str] = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True

        # Wire up CAN bus callbacks
        self._can.on_idrive(self._handle_idrive)
        self._can.on_steering(self._handle_steering)
        self._can.on_display_change(self._handle_display)
        await self._can.start()

        # Init and show launcher
        self._launcher.init()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        await asyncio.gather(
            self._launcher.run(),
            self._monitor_usb(),
        )

    async def shutdown(self) -> None:
        log.info("Shutting down")
        self._running = False
        await self._deactivate_current_app()
        await self._can.stop()
        self._launcher.shutdown()

    # ------------------------------------------------------------------
    # CAN input handlers
    # ------------------------------------------------------------------

    async def _handle_idrive(self, event: IDriveEvent) -> None:
        if self._active_app is None:
            # Launcher: rotate highlights tile
            if event.rotation != 0:
                self._launcher.rotate(event.rotation)
            elif event.button == IDriveButton.PUSH:
                await self._launcher.select()
        else:
            # In-app: route to the active app
            if event.button == IDriveButton.BACK:
                await self._go_back_to_launcher()
            elif self._active_app == "browser" and event.rotation != 0:
                await self._browser_adapter.scroll(event.rotation * 3)
            elif self._active_app == "video":
                if event.button == IDriveButton.PUSH:
                    await self._video.toggle_pause()
                elif event.rotation != 0:
                    await self._video.seek(event.rotation * 10.0)

    async def _handle_steering(self, event: SteeringEvent) -> None:
        if self._active_app == "video":
            if event.button == SteeringButton.NEXT:
                await self._video.next_file()
            elif event.button == SteeringButton.PREV:
                await self._video.prev_file()

    async def _handle_display(self, display_on: bool) -> None:
        log.info("NBT display %s", "on" if display_on else "off")

    # ------------------------------------------------------------------
    # App switching
    # ------------------------------------------------------------------

    async def _on_app_selected(self, app_key: str) -> None:
        await self._deactivate_current_app()
        self._active_app = app_key
        log.info("Activating app: %s", app_key)

        if app_key == "carplay":
            await self._carplay_mgr.activate()
        elif app_key == "browser":
            await self._browser.start()
        elif app_key == "video":
            await self._video.start()
            await self._video.load_directory()

    async def _deactivate_current_app(self) -> None:
        if self._active_app == "carplay":
            await self._carplay_mgr.deactivate()
        elif self._active_app == "browser":
            await self._browser.stop()
        elif self._active_app == "video":
            await self._video.stop()
        self._active_app = None

    async def _go_back_to_launcher(self) -> None:
        await self._deactivate_current_app()
        self._launcher.draw_initial()

    # ------------------------------------------------------------------
    # USB auto-mount watcher (notifies video player of new drives)
    # ------------------------------------------------------------------

    async def _monitor_usb(self) -> None:
        """Poll /media for new USB drives and tell the video player."""
        known: set[str] = set()
        while self._running:
            media = Path("/media")
            if media.exists():
                current = {p.name for p in media.iterdir() if p.is_dir()}
                new = current - known
                for drive in new:
                    log.info("USB drive mounted: %s", drive)
                    if self._active_app == "video":
                        await self._video.load_directory(media / drive)
                known = current
            await asyncio.sleep(3)


def main() -> None:
    parser = argparse.ArgumentParser(description="BMW NBT iDrive Extended")
    parser.add_argument("--config",  default="config/nbt.yaml", help="Config file path")
    parser.add_argument("--sim",     action="store_true",        help="Simulation mode (no CAN)")
    parser.add_argument("--app",     choices=["carplay", "browser", "video"],
                        help="Launch directly into an app (skip launcher)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(Path(args.config))

    display_env = {
        "DISPLAY": os.environ.get("DISPLAY", ":0"),
        "XAUTHORITY": os.environ.get("XAUTHORITY", str(Path.home() / ".Xauthority")),
    }
    if "WAYLAND_DISPLAY" in os.environ:
        display_env["WAYLAND_DISPLAY"] = os.environ["WAYLAND_DISPLAY"]
        display_env["XDG_RUNTIME_DIR"] = os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")

    app = NBTExtendApp(config, display_env, sim=args.sim)

    if args.app:
        async def _run_with_direct_launch() -> None:
            await app._can.start()
            await app._on_app_selected(args.app)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            await app.shutdown()
        asyncio.run(_run_with_direct_launch())
    else:
        asyncio.run(app.run())


if __name__ == "__main__":
    main()
