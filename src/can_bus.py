"""
BMW NBT K-CAN interface for the F30/F34 platform (2012-2016).

Physical setup: MCP2515 CAN HAT on Raspberry Pi, connected to the
K-CAN bus (100 kbps) via the OBD-II port or by tapping the bus behind
the head unit.  The K-CAN carries iDrive controller events, steering
wheel multimedia buttons, and display state messages.

Relevant CAN IDs on K-CAN (100 kbps):
  0x21A  – iDrive controller (rotary encoder + push + touch)
  0x1D6  – Steering wheel multimedia buttons
  0x273  – Display on/off state from instrument cluster
  0x130  – Gear selector position
  0x193  – Speed / RPM (used to gate video-in-motion)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Coroutine

try:
    import can
    CAN_AVAILABLE = True
except ImportError:
    CAN_AVAILABLE = False

log = logging.getLogger(__name__)


class IDriveButton(IntEnum):
    NONE = 0x00
    PUSH = 0x01        # center press
    MENU = 0x02        # long-press / menu button
    BACK = 0x04        # tilt-left long / back
    OPTION = 0x08      # tilt-right long / option

class SteeringButton(IntEnum):
    NONE      = 0x00
    VOL_UP    = 0x01
    VOL_DOWN  = 0x02
    NEXT      = 0x10
    PREV      = 0x20
    VOICE     = 0x40
    PHONE     = 0x80


@dataclass
class IDriveEvent:
    rotation: int = 0           # positive = clockwise, negative = counter-clockwise
    button: IDriveButton = IDriveButton.NONE


@dataclass
class SteeringEvent:
    button: SteeringButton = SteeringButton.NONE
    long_press: bool = False


@dataclass
class VehicleState:
    speed_kph: float = 0.0
    gear: str = "P"
    display_on: bool = True
    in_motion: bool = False

    def update_speed(self, raw: int) -> None:
        self.speed_kph = raw * 0.1
        self.in_motion = self.speed_kph > 3.0


# Type aliases for callbacks
IDriveCallback = Callable[[IDriveEvent], Coroutine]
SteeringCallback = Callable[[SteeringEvent], Coroutine]
DisplayCallback = Callable[[bool], Coroutine]


GEAR_MAP = {0x00: "P", 0x01: "R", 0x02: "N", 0x03: "D", 0x04: "S"}

# K-CAN bus speed for F-series BMWs
K_CAN_BITRATE = 100_000


class BMWCanBus:
    """Async wrapper around python-can for the BMW K-CAN bus."""

    def __init__(self, channel: str = "can0", bitrate: int = K_CAN_BITRATE) -> None:
        self._channel = channel
        self._bitrate = bitrate
        self._bus: "can.BusABC | None" = None
        self._reader: "can.AsyncBufferedReader | None" = None
        self._notifier: "can.Notifier | None" = None

        self._idrive_callbacks: list[IDriveCallback] = []
        self._steering_callbacks: list[SteeringCallback] = []
        self._display_callbacks: list[DisplayCallback] = []

        self.state = VehicleState()
        self._running = False

    # ------------------------------------------------------------------
    # Subscription helpers
    # ------------------------------------------------------------------

    def on_idrive(self, cb: IDriveCallback) -> None:
        self._idrive_callbacks.append(cb)

    def on_steering(self, cb: SteeringCallback) -> None:
        self._steering_callbacks.append(cb)

    def on_display_change(self, cb: DisplayCallback) -> None:
        self._display_callbacks.append(cb)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not CAN_AVAILABLE:
            log.warning("python-can not installed; running in simulation mode")
            self._running = True
            asyncio.create_task(self._simulate())
            return

        loop = asyncio.get_running_loop()
        self._bus = can.interface.Bus(
            channel=self._channel,
            bustype="socketcan",
            bitrate=self._bitrate,
        )
        self._reader = can.AsyncBufferedReader()
        self._notifier = can.Notifier(self._bus, [self._reader], loop=loop)
        self._running = True
        asyncio.create_task(self._receive_loop())
        log.info("CAN bus started on %s @ %d bps", self._channel, self._bitrate)

    async def stop(self) -> None:
        self._running = False
        if self._notifier:
            self._notifier.stop()
        if self._bus:
            self._bus.shutdown()
        log.info("CAN bus stopped")

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    async def _receive_loop(self) -> None:
        while self._running:
            try:
                msg = await asyncio.wait_for(self._reader.get_message(), timeout=1.0)
                await self._dispatch(msg)
            except asyncio.TimeoutError:
                continue
            except Exception:
                log.exception("CAN receive error")

    async def _dispatch(self, msg: "can.Message") -> None:
        mid = msg.arbitration_id
        data = msg.data

        if mid == 0x21A:
            await self._parse_idrive(data)
        elif mid == 0x1D6:
            await self._parse_steering(data)
        elif mid == 0x273:
            await self._parse_display(data)
        elif mid == 0x193:
            if len(data) >= 2:
                raw_speed = (data[0] << 8) | data[1]
                self.state.update_speed(raw_speed)
        elif mid == 0x130:
            if data:
                self.state.gear = GEAR_MAP.get(data[0] & 0x0F, "?")

    async def _parse_idrive(self, data: bytes) -> None:
        """
        Byte 0: signed rotation delta (two's complement, 1 tick = 1 count)
        Byte 1: button bitmask
        """
        if len(data) < 2:
            return
        rotation = data[0] if data[0] < 128 else data[0] - 256
        button = IDriveButton(data[1] & 0x0F)
        event = IDriveEvent(rotation=rotation, button=button)
        for cb in self._idrive_callbacks:
            await cb(event)

    async def _parse_steering(self, data: bytes) -> None:
        if len(data) < 2:
            return
        raw = data[0]
        long_press = bool(data[1] & 0x01)
        button = SteeringButton(raw)
        event = SteeringEvent(button=button, long_press=long_press)
        for cb in self._steering_callbacks:
            await cb(event)

    async def _parse_display(self, data: bytes) -> None:
        if not data:
            return
        display_on = bool(data[0] & 0x01)
        if display_on != self.state.display_on:
            self.state.display_on = display_on
            for cb in self._display_callbacks:
                await cb(display_on)

    # ------------------------------------------------------------------
    # Simulation mode (no hardware)
    # ------------------------------------------------------------------

    async def _simulate(self) -> None:
        """Emit synthetic events for development without hardware."""
        import random
        log.info("CAN simulation mode active")
        await asyncio.sleep(3)
        while self._running:
            await asyncio.sleep(5)
            # Simulate a clockwise iDrive tick
            ev = IDriveEvent(rotation=random.choice([-1, 1, 0]), button=IDriveButton.NONE)
            for cb in self._idrive_callbacks:
                await cb(ev)
