"""Base class for all feature apps running on the NBT middleware."""

import asyncio
import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class BaseApp(ABC):
    """
    Each app owns one external process (or coroutine) and a display window.
    The launcher calls start()/stop() to bring apps in and out of focus.
    """

    name: str = "unnamed"

    def __init__(self, display_env: dict) -> None:
        # DISPLAY and WAYLAND_DISPLAY variables for the compositor
        self._env = display_env
        self._proc: asyncio.subprocess.Process | None = None
        self._running = False

    @abstractmethod
    async def launch(self) -> None:
        """Start the underlying process / service."""

    @abstractmethod
    async def terminate(self) -> None:
        """Gracefully stop the app."""

    async def start(self) -> None:
        if self._running:
            return
        log.info("Starting app: %s", self.name)
        await self.launch()
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        log.info("Stopping app: %s", self.name)
        await self.terminate()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def _kill_proc(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
