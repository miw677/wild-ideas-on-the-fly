"""Tests for the BMW CAN bus parser (no hardware required)."""

import asyncio
import pytest

from src.can_bus import (
    BMWCanBus,
    IDriveButton,
    IDriveEvent,
    SteeringButton,
    SteeringEvent,
    VehicleState,
)


class FakeMessage:
    def __init__(self, arb_id: int, data: bytes):
        self.arbitration_id = arb_id
        self.data = data


# ──────────────────────────────────────────────
# VehicleState
# ──────────────────────────────────────────────

def test_vehicle_state_speed_zero_is_not_in_motion():
    s = VehicleState()
    s.update_speed(0)
    assert not s.in_motion

def test_vehicle_state_slow_speed_not_in_motion():
    s = VehicleState()
    s.update_speed(25)           # 25 * 0.1 = 2.5 km/h
    assert not s.in_motion

def test_vehicle_state_driving_speed_is_in_motion():
    s = VehicleState()
    s.update_speed(50)           # 5.0 km/h
    assert s.in_motion

def test_vehicle_state_speed_value():
    s = VehicleState()
    s.update_speed(500)          # 50.0 km/h
    assert s.speed_kph == pytest.approx(50.0)


# ──────────────────────────────────────────────
# iDrive parser
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_idrive_clockwise_rotation():
    bus = BMWCanBus.__new__(BMWCanBus)
    bus._idrive_callbacks = []
    bus._steering_callbacks = []
    bus._display_callbacks = []
    bus.state = VehicleState()

    received: list[IDriveEvent] = []
    async def capture(ev): received.append(ev)
    bus._idrive_callbacks.append(capture)

    await bus._parse_idrive(bytes([0x03, 0x00]))  # +3 ticks, no button
    assert len(received) == 1
    assert received[0].rotation == 3
    assert received[0].button == IDriveButton.NONE

@pytest.mark.asyncio
async def test_idrive_counter_clockwise_rotation():
    bus = BMWCanBus.__new__(BMWCanBus)
    bus._idrive_callbacks = []
    bus._steering_callbacks = []
    bus._display_callbacks = []
    bus.state = VehicleState()

    received: list[IDriveEvent] = []
    async def capture(ev): received.append(ev)
    bus._idrive_callbacks.append(capture)

    await bus._parse_idrive(bytes([0xFF, 0x00]))  # -1 (255 = -1 in two's complement)
    assert received[0].rotation == -1

@pytest.mark.asyncio
async def test_idrive_push_button():
    bus = BMWCanBus.__new__(BMWCanBus)
    bus._idrive_callbacks = []
    bus._steering_callbacks = []
    bus._display_callbacks = []
    bus.state = VehicleState()

    received: list[IDriveEvent] = []
    async def capture(ev): received.append(ev)
    bus._idrive_callbacks.append(capture)

    await bus._parse_idrive(bytes([0x00, IDriveButton.PUSH]))
    assert received[0].button == IDriveButton.PUSH

@pytest.mark.asyncio
async def test_idrive_short_frame_ignored():
    bus = BMWCanBus.__new__(BMWCanBus)
    bus._idrive_callbacks = []
    received: list[IDriveEvent] = []
    async def capture(ev): received.append(ev)
    bus._idrive_callbacks.append(capture)

    await bus._parse_idrive(bytes([0x01]))  # only 1 byte — should be ignored
    assert len(received) == 0


# ──────────────────────────────────────────────
# Steering button parser
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_steering_vol_up():
    bus = BMWCanBus.__new__(BMWCanBus)
    bus._idrive_callbacks = []
    bus._steering_callbacks = []
    bus._display_callbacks = []
    bus.state = VehicleState()

    received: list[SteeringEvent] = []
    async def capture(ev): received.append(ev)
    bus._steering_callbacks.append(capture)

    await bus._parse_steering(bytes([SteeringButton.VOL_UP, 0x00]))
    assert received[0].button == SteeringButton.VOL_UP
    assert not received[0].long_press

@pytest.mark.asyncio
async def test_steering_long_press_flag():
    bus = BMWCanBus.__new__(BMWCanBus)
    bus._idrive_callbacks = []
    bus._steering_callbacks = []
    bus._display_callbacks = []
    bus.state = VehicleState()

    received: list[SteeringEvent] = []
    async def capture(ev): received.append(ev)
    bus._steering_callbacks.append(capture)

    await bus._parse_steering(bytes([SteeringButton.VOICE, 0x01]))
    assert received[0].long_press is True


# ──────────────────────────────────────────────
# Display state parser
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_display_on():
    bus = BMWCanBus.__new__(BMWCanBus)
    bus._idrive_callbacks = []
    bus._steering_callbacks = []
    bus._display_callbacks = []
    bus.state = VehicleState()
    bus.state.display_on = False  # start off

    received: list[bool] = []
    async def capture(state): received.append(state)
    bus._display_callbacks.append(capture)

    await bus._parse_display(bytes([0x01]))
    assert received == [True]
    assert bus.state.display_on is True

@pytest.mark.asyncio
async def test_display_no_callback_when_unchanged():
    bus = BMWCanBus.__new__(BMWCanBus)
    bus._idrive_callbacks = []
    bus._steering_callbacks = []
    bus._display_callbacks = []
    bus.state = VehicleState()
    bus.state.display_on = True  # already on

    received: list[bool] = []
    async def capture(state): received.append(state)
    bus._display_callbacks.append(capture)

    await bus._parse_display(bytes([0x01]))  # still on — no change
    assert len(received) == 0
