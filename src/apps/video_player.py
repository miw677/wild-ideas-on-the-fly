"""
Video player module — mpv with IPC socket control.

Features
--------
- Plays local files from USB drives (auto-mounted at /media/*)
- Supports MKV, MP4, AVI, MOV and most other containers
- iDrive rotation scrubs position; push = pause/resume
- Steering NEXT/PREV skips to adjacent file in the same directory
- Respects the NBT motion lock: pauses when car is moving if configured

Why mpv
-------
mpv's --input-ipc-server exposes a JSON IPC socket that lets us send
commands (loadfile, set_property, etc.) from our async control loop
without needing a GUI framework.
"""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from .base_app import BaseApp

log = logging.getLogger(__name__)

MPV_BIN = shutil.which("mpv") or "/usr/bin/mpv"
IPC_SOCKET = "/tmp/bmw-mpv.sock"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".ts", ".flv", ".webm"}

# Resolution must match NBT display (1280×480) for pixel-perfect output
NBT_RESOLUTION = (1280, 480)


class VideoPlayerApp(BaseApp):
    name = "video_player"

    def __init__(
        self,
        display_env: dict,
        media_root: str = "/media",
        motion_pause: bool = True,
        resolution: tuple[int, int] = NBT_RESOLUTION,
    ) -> None:
        super().__init__(display_env)
        self._media_root = Path(media_root)
        self._motion_pause = motion_pause
        self._resolution = resolution
        self._ipc: "_MpvIPC | None" = None
        self._playlist: list[Path] = []
        self._playlist_idx = 0

    def _build_command(self, initial_file: str | None = None) -> list[str]:
        w, h = self._resolution
        cmd = [
            MPV_BIN,
            "--fs",
            f"--geometry={w}x{h}",
            "--no-border",
            "--no-osc",
            "--osd-level=1",
            f"--input-ipc-server={IPC_SOCKET}",
            "--keep-open=yes",           # stay open after file ends
            "--hwdec=auto",              # hardware decode on Pi
            "--vo=gpu",
            "--audio-device=auto",
        ]
        if initial_file:
            cmd.append(initial_file)
        else:
            cmd.append("--idle=yes")
        return cmd

    async def launch(self) -> None:
        env = {**os.environ, **self._env}
        first = str(self._playlist[0]) if self._playlist else None
        log.info("Launching mpv (ipc=%s)", IPC_SOCKET)
        self._proc = await asyncio.create_subprocess_exec(
            *self._build_command(first),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(0.5)  # give mpv time to create the socket
        self._ipc = _MpvIPC(IPC_SOCKET)
        await self._ipc.connect()
        asyncio.create_task(self._log_stderr())

    async def terminate(self) -> None:
        if self._ipc:
            await self._ipc.send_command("quit")
            await self._ipc.close()
            self._ipc = None
        await self._kill_proc()

    # ------------------------------------------------------------------
    # Playback control (called by the input adapter)
    # ------------------------------------------------------------------

    async def toggle_pause(self) -> None:
        await self._ipc_command("cycle", "pause")

    async def seek(self, seconds: float) -> None:
        await self._ipc_command("seek", seconds)

    async def next_file(self) -> None:
        self._playlist_idx = min(self._playlist_idx + 1, len(self._playlist) - 1)
        await self._load_current()

    async def prev_file(self) -> None:
        self._playlist_idx = max(self._playlist_idx - 1, 0)
        await self._load_current()

    async def load_directory(self, directory: Path | None = None) -> None:
        root = directory or self._media_root
        self._playlist = sorted(
            p for p in root.rglob("*") if p.suffix.lower() in VIDEO_EXTENSIONS
        )
        self._playlist_idx = 0
        log.info("Playlist: %d files from %s", len(self._playlist), root)
        if self._playlist and self._ipc:
            await self._load_current()

    async def on_motion_change(self, in_motion: bool) -> None:
        if self._motion_pause and self._ipc:
            prop = "yes" if in_motion else "no"
            await self._ipc_command("set_property", "pause", prop == "yes")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_current(self) -> None:
        if not self._playlist:
            return
        path = str(self._playlist[self._playlist_idx])
        await self._ipc_command("loadfile", path)

    async def _ipc_command(self, *args: Any) -> None:
        if self._ipc:
            await self._ipc.send_command(*args)

    async def _log_stderr(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        async for line in self._proc.stderr:
            decoded = line.decode().rstrip()
            if decoded:
                log.debug("[mpv] %s", decoded)


class _MpvIPC:
    """Minimal async JSON IPC client for mpv's --input-ipc-server socket."""

    def __init__(self, socket_path: str) -> None:
        self._path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._req_id = 0

    async def connect(self) -> None:
        for attempt in range(10):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(self._path)
                log.debug("mpv IPC connected at %s", self._path)
                return
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(0.3)
        log.error("Could not connect to mpv IPC socket at %s", self._path)

    async def send_command(self, *args: Any) -> None:
        if not self._writer:
            return
        self._req_id += 1
        payload = json.dumps({"command": list(args), "request_id": self._req_id}) + "\n"
        try:
            self._writer.write(payload.encode())
            await self._writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            log.warning("mpv IPC pipe broken")

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
