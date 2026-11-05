"""UC8179 e-paper command sequences over a PanelBus.

The sequences follow Waveshare's MIT-licensed epd7in5_V2 driver.
"""

import time
from collections.abc import Callable
from typing import Final

from paperroute.ports import PanelBus
from paperroute.render.panel_bits import Box

STATUS: Final[int] = 0x71
POLL: Final[float] = 0.02  # seconds
SETTLE: Final[float] = 0.1  # seconds
SLEEP_PAUSE: Final[float] = 2  # seconds
PLANE_BYTES: Final[int] = 48_000
GREY_BOOSTER: Final[bytes] = bytes.fromhex('27271817')
INVERT: Final[bytes] = bytes(range(255, -1, -1))


class PanelTimeout(Exception):
    """The panel stayed busy for longer than the timeout."""


class Uc8179:
    """
    Runs refresh sequences on a UC8179 controller.

    The controller powers off after every refresh and goes into deep
    sleep only when asked. Every refresh starts with a reset, which
    also wakes it from deep sleep.

    Callers give 1-bit planes with 1 for black. Quick mode reads 0 as
    black, so `draw_partial` inverts both planes on the wire.

    Parameters
    ----------
    bus : PanelBus
        The wires to the controller.
    timeout : float
        Seconds to wait for the panel to finish a step.
    clock : Callable[[], float]
        Gives the time in seconds.
    """

    def __init__(
        self,
        *,
        bus: PanelBus,
        timeout: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bus = bus
        self._timeout = timeout
        self._clock = clock
        self._deep = True

    def draw_full(self, *, planes: tuple[bytes, bytes]) -> None:
        """
        Wipe the panel white with a hot wipe, then draw four greys.

        Parameters
        ----------
        planes : tuple[bytes, bytes]
            The two grey planes for the whole screen.

        Raises
        ------
        PanelTimeout
            When the panel stays busy too long.
        """
        self._full_setup()
        self._send(0xE0, '02')
        self._send(0xE5, '6E')
        self._bus.command(code=0x10, data=b'\xff' * PLANE_BYTES)
        self._bus.command(code=0x13, data=b'\x00' * PLANE_BYTES)
        self._refresh(step='wipe refresh')
        self._bus.command(code=0x04)
        self._send(0x00, '1F')
        self._send(0x50, '1007')
        self._grey_setup()
        self._bus.command(code=0x10, data=planes[0])
        self._bus.command(code=0x13, data=planes[1])
        self._refresh(step='grey refresh')
        self._rest()

    def draw_clear(self, *, bits: bytes) -> None:
        """
        Draw the whole screen in black and white, with no wipe.

        Parameters
        ----------
        bits : bytes
            The whole screen's 1-bit pixels, 1 for black.

        Raises
        ------
        PanelTimeout
            When the panel stays busy too long.
        """
        self._full_setup()
        self._send(0xE0, '02')
        self._send(0xE5, '5A')
        self._bus.command(code=0x10, data=bits.translate(INVERT))
        self._bus.command(code=0x13, data=bits)
        self._refresh(step='clear refresh')
        self._rest()

    def draw_partial(
        self, *, box: Box, old: bytes, patch: bytes
    ) -> None:
        """
        Redraw one box in black and white.

        Parameters
        ----------
        box : Box
            The area to redraw.
        old : bytes
            The box's old 1-bit pixels, 1 for black.
        patch : bytes
            The box's new 1-bit pixels, 1 for black.

        Raises
        ------
        PanelTimeout
            When the panel stays busy too long.
        """
        self._start()
        self._send(0x00, '1F')
        self._power_on()
        self._send(0xE0, '02')
        self._send(0xE5, '6E')
        self._send(0x50, 'A907')
        self._window(box=box)
        self._bus.command(code=0x10, data=old.translate(INVERT))
        self._bus.command(code=0x13, data=patch.translate(INVERT))
        self._refresh(step='partial refresh')
        self._bus.command(code=0x92)
        self._rest()

    def sleep(self) -> None:
        """Put the controller into deep sleep, unless it is already."""
        if not self._deep:
            self._send(0x07, 'A5')
        self._deep = True

    def close(self) -> None:
        """Deep-sleep, wait for it to settle, then power off and close."""
        self.sleep()
        self._bus.pause(seconds=SLEEP_PAUSE)
        self._bus.power(on=False)
        self._bus.close()

    def _send(self, code: int, data: str) -> None:
        """
        Send a command with data given as hex.

        Parameters
        ----------
        code : int
            The command byte.
        data : str
            The data bytes as hex digits.
        """
        self._bus.command(code=code, data=bytes.fromhex(data))

    def _start(self) -> None:
        """Power up and reset, which also ends any deep sleep."""
        self._bus.power(on=True)
        self._bus.reset()
        self._deep = False

    def _full_setup(self) -> None:
        """Wake the controller and set it up for a whole-screen draw."""
        self._start()
        self._send(0x06, '17172817')
        self._send(0x01, '07072817')
        self._power_on()
        self._send(0x00, '1F')
        self._send(0x61, '032001E0')
        self._send(0x15, '00')
        self._send(0x50, '1007')
        self._send(0x60, '22')

    def _power_on(self) -> None:
        """Send power-on and wait for the panel."""
        self._bus.command(code=0x04)
        self._bus.pause(seconds=SETTLE)
        self._wait(step='power on')

    def _grey_setup(self) -> None:
        """Select the four-grey driving values."""
        self._bus.command(code=0x06, data=GREY_BOOSTER)
        self._send(0xE0, '02')
        self._send(0xE5, '5F')

    def _window(self, *, box: Box) -> None:
        """
        Enter partial mode and set the window.

        Parameters
        ----------
        box : Box
            The window.
        """
        self._bus.command(code=0x91)
        right = box.right - 1
        bottom = box.bottom - 1
        corners = (box.left, right, box.top, bottom)
        data = b''.join(value.to_bytes(2) for value in corners)
        self._bus.command(code=0x90, data=data + b'\x01')

    def _refresh(self, *, step: str) -> None:
        """
        Start a display refresh and wait for it.

        Parameters
        ----------
        step : str
            Names the wait in a timeout.
        """
        self._bus.command(code=0x12)
        self._bus.pause(seconds=SETTLE)
        self._wait(step=step)

    def _rest(self) -> None:
        """Power the controller off until the next refresh."""
        self._send(0x50, 'F7')
        self._bus.command(code=0x02)
        self._wait(step='power off')

    def _wait(self, *, step: str) -> None:
        """
        Poll the status command until the panel is idle.

        Parameters
        ----------
        step : str
            Names the step in a timeout.

        Raises
        ------
        PanelTimeout
            When the panel is still busy after the timeout.
        """
        start = self._clock()
        self._bus.command(code=STATUS)
        while not self._bus.idle():
            if self._clock() - start >= self._timeout:
                raise PanelTimeout(step)
            self._bus.pause(seconds=POLL)
            self._bus.command(code=STATUS)
