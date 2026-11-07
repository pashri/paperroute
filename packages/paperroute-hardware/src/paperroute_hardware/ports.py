"""Interfaces to the panel and the knobs, and the panel's timings."""

from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray

CLEAN_AFTER: Final[int] = 30  # quick patches


@dataclass(frozen=True)
class PanelRefresh:
    """
    One attempt to refresh the e-paper panel.

    Attributes
    ----------
    full : bool
        True for a full refresh, False for any kind of patch.
    seconds : float
        How long the attempt took.
    ok : bool
        Whether the panel finished it.
    clear : bool
        True for a clear refresh. It also has ``full`` set, as it
        takes about as long as a full one.
    """

    full: bool
    seconds: float
    ok: bool
    clear: bool = False


@dataclass
class PanelTimes:
    """
    The e-paper panel's latest refresh times.

    Attributes
    ----------
    partial_s : float | None
        Seconds the latest good patch took, or None before one.
    full_s : float | None
        Seconds the latest good full refresh took, or None before one.
    ok : bool
        False after a failed refresh, until the next good one.
    """

    partial_s: float | None = None
    full_s: float | None = None
    ok: bool = True


class Display(Protocol):
    """
    Somewhere frames are shown.

    A panel driver must honour ``full`` and ``turned``; a copy shown
    elsewhere, such as on a phone, may ignore both.
    """

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
        quick: bool = False,
        asleep: bool = False,
        clear: bool = False,
        clean_after: int = CLEAN_AFTER,
    ) -> None:
        """
        Show a frame.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels, drawn the right way up.
        full : bool
            Whether to do a full refresh, clearing ghosting, rather than
            a partial one.
        turned : bool
            Whether to show the frame upside down, for a panel mounted
            the other way round.
        quick : bool
            A knob was turned just now; a panel may trade grey for speed.
        asleep : bool
            The device is in Sleep mode; a panel may deep-sleep after
            drawing.
        clear : bool
            Whether to do a clear refresh: the whole screen in black and
            white, with no wipe. A full refresh wins over it.
        clean_after : int
            Quick patches a panel draws before a cleaning full refresh.
        """

    def settle(self, *, timeout: float) -> bool:
        """
        Wait until every frame shown so far is on the panel.

        Parameters
        ----------
        timeout : float
            Seconds to wait at most.

        Returns
        -------
        bool
            True when it is.
        """


class PanelBus(Protocol):
    """The wires to a UC8179 e-paper controller."""

    def reset(self) -> None:
        """Pulse the reset pin: high 20 ms, low 2 ms, high 20 ms."""

    def command(self, *, code: int, data: bytes = b'') -> None:
        """
        Send a command byte, then its data.

        Parameters
        ----------
        code : int
            The command byte, sent with the data/command pin low.
        data : bytes
            The data bytes, sent with the pin high.
        """

    def idle(self) -> bool:
        """
        Read the busy pin.

        Returns
        -------
        bool
            True when the pin reads 1, which means the panel is idle.
        """

    def pause(self, *, seconds: float) -> None:
        """
        Wait.

        Parameters
        ----------
        seconds : float
            How long to wait.
        """

    def power(self, *, on: bool) -> None:
        """
        Switch the panel's power pin.

        Parameters
        ----------
        on : bool
            True for on.
        """

    def close(self) -> None:
        """Release the pins and the SPI device."""


@dataclass(frozen=True)
class LineEdge:
    """
    A GPIO input line changing level.

    Attributes
    ----------
    line : int
        The BCM line offset.
    rising : bool
        True when the line went from 0 to 1, False when it fell.
    at : float
        When it changed, in seconds on the monotonic clock.
    """

    line: int
    rising: bool
    at: float


class KnobLines(Protocol):
    """The GPIO input lines the two knobs are wired to."""

    def read(self, *, timeout: float) -> list[LineEdge]:
        """
        Wait for the lines to change, then give the changes.

        Parameters
        ----------
        timeout : float
            Seconds to wait for a first change.

        Returns
        -------
        list[LineEdge]
            The changes in the order they happened; empty when none
            came within the timeout.
        """

    def level(self, *, line: int) -> bool:
        """
        Read a line.

        Parameters
        ----------
        line : int
            The BCM line offset.

        Returns
        -------
        bool
            True when the line reads 1.
        """

    def close(self) -> None:
        """Release the lines."""
