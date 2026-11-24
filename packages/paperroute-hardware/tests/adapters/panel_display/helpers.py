"""Shared helpers for the panel display tests."""

import threading
from dataclasses import dataclass, field
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_hardware.adapters.panel_display import PanelDisplay
from paperroute_hardware.ports import CLEAN_AFTER, PanelRefresh
from paperroute_hardware.render.panel_bits import Box

WAIT: Final[float] = 5  # seconds


BOX_A: Final[Box] = Box(left=16, top=10, right=32, bottom=20)


BOX_B: Final[Box] = Box(left=400, top=300, right=424, bottom=310)


QUICK_RETRY: Final[float] = 1 / 16  # seconds


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        """
        Read the time.

        Returns
        -------
        float
            Seconds.
        """
        return self.now


@dataclass
class GatedController:
    """
    A panel controller that records draws and waits on a gate in each.

    Attributes
    ----------
    calls : list[tuple[str, object]]
        ('full', planes), ('partial', (box, patch)), ('clear', bits)
        and ('sleep', None), in the order they were asked for.
    olds : list[bytes]
        The `old` bits of each partial, in order.
    gate : threading.Event
        Each draw waits for this; it starts set.
    entered : threading.Event
        Set when a draw starts.
    failures : list[Exception]
        Raised by the next draws, one each, in order.
    closed : bool
        Whether `close` was called.
    """

    calls: list[tuple[str, object]] = field(default_factory=list)
    olds: list[bytes] = field(default_factory=list)
    gate: threading.Event = field(default_factory=threading.Event)
    entered: threading.Event = field(default_factory=threading.Event)
    failures: list[Exception] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self) -> None:
        """Open the gate."""
        self.gate.set()

    def draw(self, *, kind: str, args: object) -> None:
        """
        Record a draw once the gate opens, raising if a failure waits.

        Parameters
        ----------
        kind : str
            'full', 'partial' or 'clear'.
        args : object
            What it was given.

        Raises
        ------
        Exception
            The next of `failures`, if any.
        """
        self.entered.set()
        assert self.gate.wait(timeout=WAIT)
        self.calls.append((kind, args))
        if self.failures:
            raise self.failures.pop(0)

    def draw_full(self, *, planes: tuple[bytes, bytes]) -> None:
        """
        Record a full refresh.

        Parameters
        ----------
        planes : tuple[bytes, bytes]
            The grey planes.
        """
        self.draw(kind='full', args=planes)

    def draw_partial(
        self, *, box: Box, old: bytes, patch: bytes
    ) -> None:
        """
        Record a 1-bit patch.

        Parameters
        ----------
        box : Box
            The area.
        old : bytes
            The bits the panel shows now.
        patch : bytes
            The bits.
        """
        self.olds.append(old)
        self.draw(kind='partial', args=(box, patch))

    def draw_clear(self, *, bits: bytes) -> None:
        """
        Record a clear refresh.

        Parameters
        ----------
        bits : bytes
            The whole screen's bits.
        """
        self.draw(kind='clear', args=bits)

    def sleep(self) -> None:
        """Record a deep sleep."""
        self.calls.append(('sleep', None))

    def close(self) -> None:
        """Record the close."""
        self.closed = True

    def kinds(self) -> list[str]:
        """
        List the kinds of draw asked for.

        Returns
        -------
        list[str]
            One kind per draw.
        """
        return [kind for kind, _ in self.calls]


@dataclass
class Rig:
    """
    A panel display on a gated controller and a hand-moved clock.

    Attributes
    ----------
    display : PanelDisplay
        The display.
    controller : GatedController
        Its controller.
    clock : Clock
        Its clock.
    reports : list[PanelRefresh]
        Refreshes handed to `report`.
    """

    display: PanelDisplay
    controller: GatedController
    clock: Clock
    reports: list[PanelRefresh]

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
        Show a frame and wait for the panel to settle.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            The frame.
        full : bool
            Whether to ask for a full refresh.
        turned : bool
            Whether to turn it.
        quick : bool
            Whether the knob was just turned.
        asleep : bool
            Whether the device is in Sleep mode.
        clear : bool
            Whether to ask for a clear refresh.
        clean_after : int
            Quick patches before a cleaning full refresh.
        """
        self.display.show(
            frame=frame,
            full=full,
            turned=turned,
            quick=quick,
            asleep=asleep,
            clear=clear,
            clean_after=clean_after,
        )
        assert self.display.settle(timeout=WAIT)


def show_settle_show(*, display: PanelDisplay) -> None:
    """
    Show a white frame, wait for it to settle, then show another.

    Parameters
    ----------
    display : PanelDisplay
        The display to drive.
    """
    display.show(frame=white())
    assert display.settle(timeout=WAIT)
    display.show(frame=white())


def white() -> NDArray[np.uint8]:
    """
    Make an all-white frame.

    Returns
    -------
    NDArray[np.uint8]
        The frame.
    """
    return np.full((480, 800), 255, dtype=np.uint8)


def painted(
    *,
    boxes: tuple[Box, ...],
    grey: int,
    frame: NDArray[np.uint8] | None = None,
) -> NDArray[np.uint8]:
    """
    Fill boxes of a frame with one grey.

    Parameters
    ----------
    boxes : tuple[Box, ...]
        The areas.
    grey : int
        The value.
    frame : NDArray[np.uint8] | None
        The frame to start from, or None for white.

    Returns
    -------
    NDArray[np.uint8]
        A new frame.
    """
    out = white() if frame is None else frame.copy()
    for box in boxes:
        out[box.top : box.bottom, box.left : box.right] = grey
    return out


def flicker(
    *,
    rig: Rig,
    count: int,
    quick: bool = False,
    turned: bool = False,
    clean_after: int = CLEAN_AFTER,
) -> NDArray[np.uint8]:
    """
    Show frames that alternate black and white in one box.

    The first one is black, so each one is a change from a white frame.

    Parameters
    ----------
    rig : Rig
        The rig.
    count : int
        How many frames to show.
    quick : bool
        Whether each one is quick.
    turned : bool
        Whether each one is turned.
    clean_after : int
        Quick patches before a cleaning full refresh.

    Returns
    -------
    NDArray[np.uint8]
        The last frame shown.
    """
    frame = white()
    for index in range(count):
        frame = painted(boxes=(BOX_A,), grey=255 if index % 2 else 0)
        rig.show(
            frame=frame,
            quick=quick,
            turned=turned,
            clean_after=clean_after,
        )
    return frame
