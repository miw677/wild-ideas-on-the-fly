"""
Launcher home screen rendered with pygame.

This is the "idle" surface displayed when no feature app is active.
It shows three tiles (CarPlay / Browser / Video) and responds to
iDrive rotation (highlight) and iDrive push (activate).

Resolution is fixed at 1280×480 to match the NBT display.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Coroutine

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

log = logging.getLogger(__name__)

# NBT display resolution
WIDTH, HEIGHT = 1280, 480

# Colour palette (BMW-ish dark theme)
BG_COLOR      = (10,  10,  15)
TILE_INACTIVE = (30,  30,  40)
TILE_ACTIVE   = (0,   112, 195)   # BMW iBlue
TILE_BORDER   = (60,  60,  80)
TEXT_COLOR    = (230, 230, 240)
SUBTEXT_COLOR = (140, 140, 160)

FONT_TITLE = 42
FONT_SUB   = 22


@dataclass
class Tile:
    label: str
    sublabel: str
    icon_char: str          # Unicode symbol used as icon
    app_key: str


TILES = [
    Tile("CarPlay",     "iPhone mirroring",    "\U0001F4F1", "carplay"),
    Tile("Web Browser", "Full internet access", "\U0001F310", "browser"),
    Tile("Video",       "USB media playback",  "\U0001F3AC", "video"),
]

AppLaunchCallback = Callable[[str], Coroutine]


class Launcher:
    """Pygame-based home screen with iDrive rotation/push navigation."""

    def __init__(self, on_launch: AppLaunchCallback) -> None:
        self._on_launch = on_launch
        self._selected = 0
        self._screen: "pygame.Surface | None" = None
        self._font_title: "pygame.font.Font | None" = None
        self._font_sub: "pygame.font.Font | None" = None
        self._font_icon: "pygame.font.Font | None" = None
        self._running = False

    def init(self) -> bool:
        if not PYGAME_AVAILABLE:
            log.warning("pygame not installed; launcher UI disabled")
            return False
        pygame.init()
        self._screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.NOFRAME)
        pygame.display.set_caption("BMW iDrive Extended")
        self._font_title = pygame.font.SysFont("DejaVu Sans", FONT_TITLE, bold=True)
        self._font_sub   = pygame.font.SysFont("DejaVu Sans", FONT_SUB)
        self._font_icon  = pygame.font.SysFont("Symbola",     80)
        self._running = True
        return True

    def shutdown(self) -> None:
        self._running = False
        if PYGAME_AVAILABLE:
            pygame.quit()

    # Called by the CAN bus handler
    def rotate(self, delta: int) -> None:
        self._selected = (self._selected + delta) % len(TILES)
        self._draw()

    async def select(self) -> None:
        key = TILES[self._selected].app_key
        await self._on_launch(key)

    def draw_initial(self) -> None:
        self._draw()

    def _draw(self) -> None:
        if not self._screen:
            return

        self._screen.fill(BG_COLOR)
        self._draw_header()

        tile_w = WIDTH // len(TILES)
        for i, tile in enumerate(TILES):
            self._draw_tile(tile, i, tile_w, i == self._selected)

        pygame.display.flip()

    def _draw_header(self) -> None:
        assert self._font_sub
        label = self._font_sub.render(
            "BMW iDrive Extended  •  rotate iDrive to select, push to open",
            True, SUBTEXT_COLOR,
        )
        self._screen.blit(label, (20, 14))

    def _draw_tile(self, tile: Tile, idx: int, tile_w: int, active: bool) -> None:
        assert self._screen and self._font_title and self._font_sub and self._font_icon

        x = idx * tile_w + 16
        y = 60
        w = tile_w - 32
        h = HEIGHT - 80

        color = TILE_ACTIVE if active else TILE_INACTIVE
        rect = pygame.Rect(x, y, w, h)
        pygame.draw.rect(self._screen, color, rect, border_radius=16)
        pygame.draw.rect(self._screen, TILE_BORDER, rect, width=2, border_radius=16)

        # Icon
        try:
            icon_surf = self._font_icon.render(tile.icon_char, True, TEXT_COLOR)
            ix = x + (w - icon_surf.get_width()) // 2
            self._screen.blit(icon_surf, (ix, y + 60))
        except Exception:
            pass  # font may not have the glyph; skip silently

        # Title
        title_surf = self._font_title.render(tile.label, True, TEXT_COLOR)
        tx = x + (w - title_surf.get_width()) // 2
        self._screen.blit(title_surf, (tx, y + 170))

        # Subtitle
        sub_surf = self._font_sub.render(tile.sublabel, True, SUBTEXT_COLOR)
        sx = x + (w - sub_surf.get_width()) // 2
        self._screen.blit(sub_surf, (sx, y + 230))

    async def run(self) -> None:
        """Event loop for pygame events (keyboard fallback for testing)."""
        if not PYGAME_AVAILABLE or not self._screen:
            return
        self.draw_initial()
        while self._running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_LEFT:
                        self.rotate(-1)
                    elif event.key == pygame.K_RIGHT:
                        self.rotate(1)
                    elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        await self.select()
                    elif event.key == pygame.K_ESCAPE:
                        self._running = False
            await asyncio.sleep(1 / 30)  # 30 fps is plenty for a static menu
