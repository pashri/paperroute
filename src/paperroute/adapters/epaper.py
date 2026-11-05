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


class PanelTimeout(Exception):
    """The panel stayed busy for longer than the timeout."""


class Uc8179:
    """
    Runs refresh sequences on a UC8179 controller.

    The controller goes into deep sleep after every refresh; the next
    one wakes it with a reset.

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

    def draw_full(self, *, planes: tuple[bytes, bytes]) -> None:
        """
        Wipe the panel white, then draw four greys.

        Parameters
        ----------
        planes : tuple[bytes, bytes]
            The two grey planes for the whole screen.

        Raises
        ------
        PanelTimeout
            When the panel stays busy too long.
        """
        self._wake()
        self._send(0x01, '07072817')
        self._power_on()
        self._send(0x00, '1F')
        self._send(0x61, '032001E0')
        self._send(0x15, '00')
        self._send(0x50, '1007')
        self._send(0x60, '22')
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
        self._sleep()

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
            The box's 1-bit pixels as the panel shows them now.
        patch : bytes
            The box's new 1-bit pixels.

        Raises
        ------
        PanelTimeout
            When the panel stays busy too long.
        """
        self._bus.power(on=True)
        self._bus.reset()
        self._send(0x00, '1F')
        self._power_on()
        self._send(0xE0, '02')
        self._send(0xE5, '6E')
        self._send(0x50, 'A907')
        self._window(box=box)
        self._bus.command(code=0x10, data=old)
        self._bus.command(code=0x13, data=patch)
        self._refresh(step='partial refresh')
        self._bus.command(code=0x92)
        self._sleep()

    def draw_grey_patch(
        self, *, box: Box, planes: tuple[bytes, bytes]
    ) -> None:
        """
        Redraw one box in four greys.

        Parameters
        ----------
        box : Box
            The area to redraw.
        planes : tuple[bytes, bytes]
            The two grey planes cut to the box.

        Raises
        ------
        PanelTimeout
            When the panel stays busy too long.
        """
        self._bus.power(on=True)
        self._bus.reset()
        self._send(0x00, '1F')
        self._send(0x50, '1007')
        self._power_on()
        self._grey_setup()
        self._window(box=box)
        self._bus.command(code=0x10, data=planes[0])
        self._bus.command(code=0x13, data=planes[1])
        self._refresh(step='grey patch refresh')
        self._bus.command(code=0x92)
        self._sleep()

    def close(self) -> None:
        """Wait for deep sleep to settle, then power off and close."""
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

    def _wake(self) -> None:
        """Power up, reset and set the full-screen driving values."""
        self._bus.power(on=True)
        self._bus.reset()
        self._send(0x06, '17172817')

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

    def _sleep(self) -> None:
        """Put the controller into deep sleep."""
        self._send(0x50, 'F7')
        self._bus.command(code=0x02)
        self._wait(step='power off')
        self._send(0x07, 'A5')

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
