"""Tests for the UC8179 command sequences."""

from typing import cast

import pytest

from paperroute.adapters.epaper import PanelTimeout, Uc8179
from paperroute.render.panel_bits import Box
from tests.fakes import FakePanelBus

WINDOW = bytes.fromhex('00C80257' '00A0013F' '01')
BOX = Box(200, 160, 600, 320)


def make(*, bus: FakePanelBus) -> Uc8179:
    """
    Build a controller on a fake bus with its fake clock.

    Parameters
    ----------
    bus : FakePanelBus
        The bus.

    Returns
    -------
    Uc8179
        The controller.
    """
    return Uc8179(bus=bus, clock=lambda: bus.now_s)


def codes(*, bus: FakePanelBus) -> list[tuple[int, int]]:
    """
    List each command as its code and data length.

    Parameters
    ----------
    bus : FakePanelBus
        The bus.

    Returns
    -------
    list[tuple[int, int]]
        Code and data length, in order.
    """
    return [(code, len(data)) for code, data in sent(bus=bus)]


def sent(*, bus: FakePanelBus) -> list[tuple[int, bytes]]:
    """
    List the commands sent.

    Parameters
    ----------
    bus : FakePanelBus
        The bus.

    Returns
    -------
    list[tuple[int, bytes]]
        Code and data, in order.
    """
    return [
        cast(tuple[int, bytes], value)
        for kind, value in bus.log
        if kind == 'command'
    ]


def test_draw_full_sequence() -> None:
    """Every command of the wipe and grey passes, in order."""
    bus = FakePanelBus()
    make(bus=bus).draw_full(planes=(b'a' * 48000, b'b' * 48000))
    assert bus.log[:2] == [('power', True), ('reset', None)]
    assert codes(bus=bus) == [
        (0x06, 4), (0x01, 4), (0x04, 0), (0x71, 0),
        (0x00, 1), (0x61, 4), (0x15, 1), (0x50, 2), (0x60, 1),
        (0x10, 48000), (0x13, 48000), (0x12, 0), (0x71, 0),
        (0x04, 0),
        (0x00, 1), (0x50, 2), (0x06, 4), (0xE0, 1), (0xE5, 1),
        (0x10, 48000), (0x13, 48000), (0x12, 0), (0x71, 0),
        (0x50, 1), (0x02, 0), (0x71, 0), (0x07, 1),
    ]
    commands = sent(bus=bus)
    assert (0x10, b'\xff' * 48000) in commands
    assert (0x13, b'\x00' * 48000) in commands
    assert (0x06, bytes.fromhex('27271817')) in commands


def test_draw_partial_window_bytes() -> None:
    """The window command carries the box edges."""
    bus = FakePanelBus()
    make(bus=bus).draw_partial(
        box=BOX, old=b'o' * 20000, patch=b'p' * 20000,
    )
    assert (0x90, WINDOW) in sent(bus=bus)
    assert (0x10, b'o' * 20000) in sent(bus=bus)
    assert (0x13, b'p' * 20000) in sent(bus=bus)
    assert codes(bus=bus) == [
        (0x00, 1), (0x04, 0), (0x71, 0), (0xE0, 1), (0xE5, 1),
        (0x50, 2), (0x91, 0), (0x90, 9), (0x10, 20000),
        (0x13, 20000), (0x12, 0),
        (0x71, 0), (0x92, 0), (0x50, 1), (0x02, 0), (0x71, 0),
        (0x07, 1),
    ]


def test_wait_resends_status_command() -> None:
    """Each poll sends the status command again."""
    bus = FakePanelBus(busy_polls=3)
    make(bus=bus).draw_partial(box=BOX, old=b'', patch=b'')
    first = codes(bus=bus)[:6]
    assert first[:2] == [(0x00, 1), (0x04, 0)]
    assert first[2:] == [(0x71, 0)] * 4


def test_stuck_panel_times_out() -> None:
    """A panel that never goes idle raises, naming the step."""
    bus = FakePanelBus(stuck=True)
    with pytest.raises(PanelTimeout, match='power on'):
        make(bus=bus).draw_partial(box=BOX, old=b'', patch=b'')
    assert bus.now_s >= 30


def test_full_sends_power_on_before_greys() -> None:
    """A bare power-on separates the wipe from the greys."""
    bus = FakePanelBus()
    make(bus=bus).draw_full(planes=(b'', b''))
    commands = sent(bus=bus)
    index = [i for i, (code, _) in enumerate(commands) if code == 0x12][0]
    assert commands[index + 1] == (0x71, b'')
    assert commands[index + 2] == (0x04, b'')
    assert commands[index + 3] == (0x00, b'\x1f')


def test_close_powers_off() -> None:
    """Closing waits, drops the power pin and closes the bus."""
    bus = FakePanelBus()
    make(bus=bus).close()
    assert bus.log == [('pause', 2), ('power', False), ('close', None)]


def test_draw_grey_patch_sequence() -> None:
    """The grey patch sends its window and both planes."""
    bus = FakePanelBus()
    make(bus=bus).draw_grey_patch(box=BOX, planes=(b'a' * 5, b'b' * 5))
    assert codes(bus=bus) == [
        (0x00, 1), (0x50, 2), (0x04, 0), (0x71, 0), (0x06, 4),
        (0xE0, 1), (0xE5, 1), (0x91, 0), (0x90, 9), (0x10, 5),
        (0x13, 5), (0x12, 0), (0x71, 0), (0x92, 0), (0x50, 1),
        (0x02, 0), (0x71, 0), (0x07, 1),
    ]
    assert (0x90, WINDOW) in sent(bus=bus)
