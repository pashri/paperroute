"""Tests for the UC8179 command sequences."""

from typing import cast

import pytest

from paperroute.adapters.epaper import PanelTimeout, Uc8179
from paperroute.render.panel_bits import Box
from paperroute_testkit.fakes import FakePanelBus

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
        (0xE0, 1), (0xE5, 1),
        (0x10, 48000), (0x13, 48000), (0x12, 0), (0x71, 0),
        (0x04, 0),
        (0x00, 1), (0x50, 2), (0x06, 4), (0xE0, 1), (0xE5, 1),
        (0x10, 48000), (0x13, 48000), (0x12, 0), (0x71, 0),
        (0x50, 1), (0x02, 0), (0x71, 0),
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
    assert (0x10, bytes([ord('o') ^ 0xFF]) * 20000) in sent(bus=bus)
    assert (0x13, bytes([ord('p') ^ 0xFF]) * 20000) in sent(bus=bus)
    assert codes(bus=bus) == [
        (0x00, 1), (0x04, 0), (0x71, 0), (0xE0, 1), (0xE5, 1),
        (0x50, 2), (0x91, 0), (0x90, 9), (0x10, 20000),
        (0x13, 20000), (0x12, 0),
        (0x71, 0), (0x92, 0), (0x50, 1), (0x02, 0), (0x71, 0),
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


REST: list[tuple[int, bytes]] = [(0x50, b'\xf7'), (0x02, b''), (0x71, b'')]
DEEP_SLEEP: tuple[int, bytes] = (0x07, b'\xa5')
SMALL = Box(left=0, top=0, right=8, bottom=1)


def test_draw_partial_inverts_both_planes() -> None:
    """Quick mode reads 0 as black, so both planes go out inverted."""
    bus = FakePanelBus()
    make(bus=bus).draw_partial(box=SMALL, old=b'\x0f', patch=b'\xf0')
    commands = sent(bus=bus)
    assert (0x10, b'\xf0') in commands
    assert (0x13, b'\x0f') in commands


def test_refresh_rests_without_deep_sleep() -> None:
    """Partial and full refreshes end with a power off, not deep sleep."""
    bus = FakePanelBus()
    panel = make(bus=bus)
    panel.draw_partial(box=SMALL, old=b'\x00', patch=b'\x00')
    assert sent(bus=bus)[-3:] == REST
    panel.draw_full(planes=(b'', b''))
    assert sent(bus=bus)[-3:] == REST
    assert all(code != 0x07 for code, _ in sent(bus=bus))


def test_sleep_deep_sleeps_once() -> None:
    """A second sleep in a row sends nothing."""
    bus = FakePanelBus()
    panel = make(bus=bus)
    panel.draw_partial(box=SMALL, old=b'\x00', patch=b'\x00')
    panel.sleep()
    panel.sleep()
    assert sent(bus=bus).count(DEEP_SLEEP) == 1


def test_close_deep_sleeps_once() -> None:
    """Close deep-sleeps an awake panel, and only once."""
    bus = FakePanelBus()
    panel = make(bus=bus)
    panel.draw_full(planes=(b'', b''))
    panel.close()
    assert sent(bus=bus).count(DEEP_SLEEP) == 1
    bus = FakePanelBus()
    panel = make(bus=bus)
    panel.draw_full(planes=(b'', b''))
    panel.sleep()
    panel.close()
    assert sent(bus=bus).count(DEEP_SLEEP) == 1
    bus = FakePanelBus()
    make(bus=bus).close()
    assert DEEP_SLEEP not in sent(bus=bus)


def test_draw_after_sleep_resets() -> None:
    """A draw after deep sleep wakes the panel with a reset."""
    bus = FakePanelBus()
    panel = make(bus=bus)
    panel.draw_full(planes=(b'', b''))
    panel.sleep()
    bus.log.clear()
    panel.draw_partial(box=SMALL, old=b'\x00', patch=b'\x00')
    assert bus.log[:2] == [('power', True), ('reset', None)]
    panel.sleep()
    assert sent(bus=bus).count(DEEP_SLEEP) == 1


def test_draw_full_hot_wipe() -> None:
    """The wipe is driven hot and the greys with their own setting."""
    bus = FakePanelBus()
    make(bus=bus).draw_full(planes=(b'', b''))
    commands = sent(bus=bus)
    refreshes = [i for i, (code, _) in enumerate(commands) if code == 0x12]
    wipe = commands[: refreshes[0]]
    grey = commands[refreshes[0] : refreshes[1]]
    assert wipe.index((0xE0, b'\x02')) > wipe.index((0x60, b'\x22'))
    assert (0xE5, b'\x6e') in wipe
    assert (0xE5, b'\x5f') in grey


def test_draw_clear_sequence() -> None:
    """A clear refresh is one warm 1-bit pass with no wipe."""
    bus = FakePanelBus()
    bits = bytes([0x0F]) * 48000
    make(bus=bus).draw_clear(bits=bits)
    commands = sent(bus=bus)
    assert bus.log[:2] == [('power', True), ('reset', None)]
    assert commands.index((0xE0, b'\x02')) > commands.index((0x60, b'\x22'))
    assert (0xE5, b'\x5a') in commands
    assert (0x10, bytes([0xF0]) * 48000) in commands
    assert (0x13, bits) in commands
    assert [code for code, _ in commands].count(0x12) == 1
    assert commands[-3:] == REST
    assert all(code != 0x07 for code, _ in commands)
