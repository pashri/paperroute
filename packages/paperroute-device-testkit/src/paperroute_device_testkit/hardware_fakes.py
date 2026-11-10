"""Hand-written stand-ins for the hardware package's ports."""

import time
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from paperroute_hardware.ports import CLEAN_AFTER, LineEdge


@dataclass
class FrameRecorder:
    """A display that keeps every frame and how it was asked for."""

    frames: list[NDArray[np.uint8]] = field(default_factory=list)
    fulls: list[bool] = field(default_factory=list)
    turns: list[bool] = field(default_factory=list)
    quicks: list[bool] = field(default_factory=list)
    sleeps: list[bool] = field(default_factory=list)
    clears: list[bool] = field(default_factory=list)
    cleans: list[int] = field(default_factory=list)

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
        Keep a frame.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            Frame.
        full : bool
            Whether a full refresh was asked for.
        turned : bool
            Whether the frame is turned upside down.
        quick : bool
            Whether a knob was turned just now.
        asleep : bool
            Whether the device is in Sleep mode.
        clear : bool
            Whether a clear refresh was asked for.
        clean_after : int
            Quick patches before a cleaning full refresh.
        """
        self.frames.append(frame)
        self.fulls.append(full)
        self.turns.append(turned)
        self.quicks.append(quick)
        self.sleeps.append(asleep)
        self.clears.append(clear)
        self.cleans.append(clean_after)

    def settle(self, *, timeout: float) -> bool:
        """
        Report that every frame is shown, which it always is.

        Parameters
        ----------
        timeout : float
            Ignored.

        Returns
        -------
        bool
            Always True.
        """
        del timeout
        return True


@dataclass
class FakePanelBus:
    """
    A panel bus that records what it is sent and never sleeps.

    Attributes
    ----------
    log : list[tuple[str, object]]
        Entries are ('command', (code, data)), ('reset', None),
        ('power', on), ('pause', seconds) and ('close', None).
    busy_polls : int
        How many `idle` calls answer False before it answers True.
    stuck : bool
        When True, `idle` never answers True.
    now_s : float
        Fake time, advanced by `pause`.
    """

    log: list[tuple[str, object]] = field(default_factory=list)
    busy_polls: int = 0
    stuck: bool = False
    now_s: float = 0.0

    def reset(self) -> None:
        """Record a reset."""
        self.log.append(('reset', None))

    def command(self, *, code: int, data: bytes = b'') -> None:
        """
        Record a command.

        Parameters
        ----------
        code : int
            The command byte.
        data : bytes
            Its data.
        """
        self.log.append(('command', (code, data)))

    def idle(self) -> bool:
        """
        Answer from `busy_polls` and `stuck`.

        Returns
        -------
        bool
            True when the panel counts as idle.
        """
        if self.stuck:
            return False
        if self.busy_polls > 0:
            self.busy_polls -= 1
            return False
        return True

    def pause(self, *, seconds: float) -> None:
        """
        Record a pause and advance the fake time.

        Parameters
        ----------
        seconds : float
            How long.
        """
        self.log.append(('pause', seconds))
        self.now_s += seconds

    def power(self, *, on: bool) -> None:
        """
        Record a power change.

        Parameters
        ----------
        on : bool
            The new state.
        """
        self.log.append(('power', on))

    def close(self) -> None:
        """Record the close."""
        self.log.append(('close', None))


@dataclass
class FakeKnobLines:
    """
    Knob lines that give scripted changes.

    Attributes
    ----------
    reads : list[list[LineEdge]]
        What each `read` gives, in turn; once used up, reads wait out
        their timeout and give nothing, or raise `error`.
    levels : dict[int, bool]
        Line levels; a line not listed reads 1, as an idle knob does.
    error : OSError | None
        Raised by `read` once `reads` is used up.
    closes : int
        How many times `close` was called.
    """

    reads: list[list[LineEdge]] = field(default_factory=list)
    levels: dict[int, bool] = field(default_factory=dict)
    error: OSError | None = None
    closes: int = 0

    def read(self, *, timeout: float) -> list[LineEdge]:
        """
        Give the next scripted changes.

        Parameters
        ----------
        timeout : float
            Seconds to wait when there are none.

        Returns
        -------
        list[LineEdge]
            The next changes, or none.

        Raises
        ------
        OSError
            When `error` is set and the script is used up.
        """
        if self.reads:
            return self.reads.pop(0)
        if self.error is not None:
            raise self.error
        time.sleep(timeout)
        return []

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
            Its level in `levels`, else True.
        """
        return self.levels.get(line, True)

    def close(self) -> None:
        """Count the close."""
        self.closes += 1
