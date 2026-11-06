"""Reading knob events off the knobs' GPIO lines on a thread."""

import logging
import threading
import time
from collections.abc import Callable
from typing import Final

from paperroute.adapters.knob_lines import DT_LINES, LINES, Role
from paperroute.core.knob_decoder import KnobDecoder
from paperroute.core.messages import Event
from paperroute.ports import KnobLines, LineEdge

logger = logging.getLogger(__name__)

WAIT: Final[float] = 0.05  # seconds a read waits, so held knobs are timed
STOP_WAIT: Final[float] = 1.0  # seconds stop waits for the thread


class KnobReader:
    """
    Turns the knobs' line changes into knob events on its own thread.

    A turn is a CLK fall: DT high at that moment means clockwise. The
    switch pulls its line low while pressed.

    Parameters
    ----------
    lines : KnobLines
        The open lines; the reader closes them on `stop`.
    decoder : KnobDecoder
        Tells presses, holds and turns apart.
    clock : Callable[[], float]
        Gives the time in seconds.
    wait : float
        Seconds each read waits for a change.
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
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def step(self) -> list[Event]:
        """
        Read the lines once and decode what changed.

        Returns
        -------
        list[Event]
            The events the changes make, then any that time settles.
        """
        edges = self.lines.read(timeout=self.wait)
        now = self.clock()
        events = [
            event
            for edge in edges
            for event in self.edge_events(edge=edge, now=now)
        ]
        return events + self.decoder.poll(now=now)

    def edge_events(self, *, edge: LineEdge, now: float) -> list[Event]:
        """
        Decode one line change.

        Parameters
        ----------
        edge : LineEdge
            The change.
        now : float
            Current time in seconds.

        Returns
        -------
        list[Event]
            The events it makes; none for a CLK rise or a DT change.
        """
        encoder, role = LINES[edge.line]
        match role:
            case Role.CLK if not edge.rising:
                clockwise = self.lines.level(line=DT_LINES[encoder])
                return self.decoder.turned(
                    encoder=encoder, clockwise=clockwise, now=now,
                )
            case Role.SW if edge.rising:
                return self.decoder.released(encoder=encoder, now=now)
            case Role.SW:
                return self.decoder.pressed(encoder=encoder, now=now)
        return []

    def run(self, *, post: Callable[[Event], None]) -> None:
        """
        Post knob events until stopped or the lines fail.

        Parameters
        ----------
        post : Callable[[Event], None]
            Receives each event, on this thread.
        """
        try:
            while not self._stopping.is_set():
                for event in self.step():
                    post(event)
        except OSError:
            logger.error('knobs stopped answering', exc_info=True)

    def start(self, *, post: Callable[[Event], None]) -> None:
        """
        Start reading on a daemon thread.

        Parameters
        ----------
        post : Callable[[Event], None]
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
