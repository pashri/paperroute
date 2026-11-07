"""Reading knob events off the knobs' GPIO lines on a thread."""

import logging
import threading
import time
from collections.abc import Callable
from typing import Final

from paperroute_hardware.adapters.knob_lines import DT_LINES, LINES, Role
from paperroute_hardware.core.knob_decoder import KnobDecoder
from paperroute_hardware.core.knob_events import KnobInput
from paperroute_hardware.ports import KnobLines, LineEdge

logger = logging.getLogger(__name__)

WAIT: Final[float] = 0.05  # seconds a read waits, so held knobs are timed
STOP_WAIT: Final[float] = 1.0  # seconds stop waits for the thread


class KnobReader:
    """
    Turns the knobs' line changes into knob events on its own thread.

    A turn is a CLK fall: DT high at that moment means clockwise. DT's
    level is followed through its own edges in event order, from its
    level when the reader is made, because by the time a batch of
    edges is read DT may have moved on. Edges are timed by their own
    timestamps, so a late read does not change a press into a hold.
    The switch pulls its line low while pressed.

    Parameters
    ----------
    lines : KnobLines
        The open lines; the reader closes them on `stop`.
    decoder : KnobDecoder
        Tells presses, holds and turns apart.
    clock : Callable[[], float]
        Gives the time in seconds, on the edges' clock.
    wait : float
        Seconds each read waits for a change.

    Attributes
    ----------
    answering : bool
        False once the lines have failed and the reader has stopped.
    """

    def __init__(
        self,
        *,
        lines: KnobLines,
        decoder: KnobDecoder,
        clock: Callable[[], float] = time.monotonic,
        wait: float = WAIT,
    ) -> None:
        self.lines = lines
        self.decoder = decoder
        self.clock = clock
        self.wait = wait
        self.dt = {
            encoder: lines.level(line=line)
            for encoder, line in DT_LINES.items()
        }
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self.answering = True

    def step(self) -> list[KnobInput]:
        """
        Read the lines once and decode what changed.

        Returns
        -------
        list[KnobInput]
            The events the changes make, then any that time settles.
        """
        edges = self.lines.read(timeout=self.wait)
        events = [
            event
            for edge in edges
            for event in self.edge_events(edge=edge)
        ]
        return events + self.decoder.poll(now=self.clock())

    def edge_events(self, *, edge: LineEdge) -> list[KnobInput]:
        """
        Decode one line change, at the time it happened.

        Parameters
        ----------
        edge : LineEdge
            The change.

        Returns
        -------
        list[KnobInput]
            The events it makes; none for a CLK rise or a DT change.
        """
        encoder, role = LINES[edge.line]
        match role:
            case Role.DT:
                self.dt[encoder] = edge.rising
            case Role.CLK if not edge.rising:
                return self.decoder.turned(
                    encoder=encoder, clockwise=self.dt[encoder], now=edge.at,
                )
            case Role.SW if edge.rising:
                return self.decoder.released(encoder=encoder, now=edge.at)
            case Role.SW:
                return self.decoder.pressed(encoder=encoder, now=edge.at)
        return []

    def run(self, *, post: Callable[[KnobInput], None]) -> None:
        """
        Post knob events until stopped or the lines fail.

        Parameters
        ----------
        post : Callable[[KnobInput], None]
            Receives each event, on this thread.
        """
        try:
            while not self._stopping.is_set():
                for event in self.step():
                    post(event)
        except OSError:
            self.answering = False
            logger.error('knobs stopped answering', exc_info=True)

    def start(self, *, post: Callable[[KnobInput], None]) -> None:
        """
        Start reading on a daemon thread.

        Parameters
        ----------
        post : Callable[[KnobInput], None]
            Receives each event, on the reader's thread, so it must be
            safe to call from there.
        """
        self._thread = threading.Thread(
            target=self.run,
            kwargs={'post': post},
            name='knobs',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread and release the lines; later calls do nothing."""
        if self._closed:
            return
        self._closed = True
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=STOP_WAIT)
        self.lines.close()
