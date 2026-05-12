"""Tests for app lifecycle and video playlist logic (no subprocesses spawned)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.apps.video_player import VideoPlayerApp, VIDEO_EXTENSIONS
from src.apps.browser import BrowserApp


DISPLAY_ENV = {"DISPLAY": ":0"}


# ──────────────────────────────────────────────
# VideoPlayerApp
# ──────────────────────────────────────────────

def test_video_extensions_cover_common_formats():
    for ext in (".mp4", ".mkv", ".avi", ".mov"):
        assert ext in VIDEO_EXTENSIONS

@pytest.mark.asyncio
async def test_video_load_directory_empty(tmp_path):
    app = VideoPlayerApp(DISPLAY_ENV, media_root=str(tmp_path))
    await app.load_directory(tmp_path)
    assert app._playlist == []

@pytest.mark.asyncio
async def test_video_load_directory_finds_videos(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"")
    (tmp_path / "ignore.txt").write_bytes(b"")
    (tmp_path / "movie.mkv").write_bytes(b"")

    app = VideoPlayerApp(DISPLAY_ENV, media_root=str(tmp_path))
    await app.load_directory(tmp_path)
    names = {p.name for p in app._playlist}
    assert "clip.mp4" in names
    assert "movie.mkv" in names
    assert "ignore.txt" not in names

@pytest.mark.asyncio
async def test_video_next_prev_clamped(tmp_path):
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / name).write_bytes(b"")

    app = VideoPlayerApp(DISPLAY_ENV, media_root=str(tmp_path))
    await app.load_directory(tmp_path)
    assert app._playlist_idx == 0

    # Can't go below 0
    await app.prev_file()  # _ipc is None so no mpv command; just index movement
    assert app._playlist_idx == 0

    await app.next_file()
    assert app._playlist_idx == 1

    await app.next_file()
    assert app._playlist_idx == 2

    # Can't go above last index
    await app.next_file()
    assert app._playlist_idx == 2

@pytest.mark.asyncio
async def test_video_motion_pause_pauses_when_moving():
    app = VideoPlayerApp(DISPLAY_ENV, motion_pause=True)
    mock_ipc = AsyncMock()
    app._ipc = mock_ipc

    await app.on_motion_change(True)
    mock_ipc.send_command.assert_awaited_once_with("set_property", "pause", True)

@pytest.mark.asyncio
async def test_video_motion_pause_disabled_does_not_pause():
    app = VideoPlayerApp(DISPLAY_ENV, motion_pause=False)
    mock_ipc = AsyncMock()
    app._ipc = mock_ipc

    await app.on_motion_change(True)
    mock_ipc.send_command.assert_not_awaited()


# ──────────────────────────────────────────────
# BrowserApp
# ──────────────────────────────────────────────

def test_browser_command_includes_homepage():
    app = BrowserApp(DISPLAY_ENV, homepage="https://example.com")
    cmd = app._build_command()
    assert "https://example.com" in cmd

def test_browser_command_includes_kiosk_flag():
    app = BrowserApp(DISPLAY_ENV)
    cmd = app._build_command()
    assert "--kiosk" in cmd

def test_browser_command_includes_profile_dir():
    app = BrowserApp(DISPLAY_ENV, profile_dir="/tmp/test-profile")
    cmd = app._build_command()
    assert any("test-profile" in part for part in cmd)

def test_browser_extra_flags_forwarded():
    app = BrowserApp(DISPLAY_ENV, extra_flags=["--disable-gpu"])
    cmd = app._build_command()
    assert "--disable-gpu" in cmd

@pytest.mark.asyncio
async def test_browser_double_start_is_idempotent():
    app = BrowserApp(DISPLAY_ENV)
    with patch.object(app, "launch", new_callable=AsyncMock) as mock_launch:
        app._running = True          # simulate already started
        await app.start()
        mock_launch.assert_not_awaited()
