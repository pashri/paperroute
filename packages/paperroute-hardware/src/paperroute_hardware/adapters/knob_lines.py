"""The Raspberry Pi wires to the two KY-040 knobs: gpiod input lines."""

from datetime import timedelta
from enum import StrEnum
from typing import Any, Final

from paperroute_hardware.core.knob_decoder import Encoder
from paperroute_hardware.ports import LineEdge


class Role(StrEnum):
    """What a knob's line carries."""

    CLK = 'clk'
    DT = 'dt'
    SW = 'sw'


LINES: Final[dict[int, tuple[Encoder, Role]]] = {
    20: (Encoder.A, Role.CLK),
    21: (Encoder.A, Role.DT),
    3: (Encoder.A, Role.SW),  # pin 5, which also wakes a halted Pi
    5: (Encoder.B, Role.CLK),
    6: (Encoder.B, Role.DT),
    16: (Encoder.B, Role.SW),
}
DT_LINES: Final[dict[Encoder, int]] = {
    encoder: line
    for line, (encoder, role) in LINES.items()
    if role is Role.DT
}
DEBOUNCE: Final[timedelta] = timedelta(milliseconds=1)
NANOSECONDS: Final[float] = 1e9  # per second


class GpiodKnobLines:  # pragma: no cover
    """
    The knobs' lines on a Raspberry Pi, pulled up and idle high.

    Parameters
    ----------
    lines : Any
        The gpiod line request.
    value : Any
        The gpiod line Value enum.
    """

    def __init__(self, *, lines: Any, value: Any) -> None:
        self.lines = lines
        self.value = value

    @classmethod
    def open(cls, *, chip: str = '/dev/gpiochip0') -> 'GpiodKnobLines':
        """
        Claim the knobs' lines, watching both edges of each.

        Parameters
        ----------
        chip : str
            The GPIO character device.

        Returns
        -------
        GpiodKnobLines
            The open lines.

        Raises
        ------
        OSError
            If the lines are unavailable, for example EBUSY when another
            process holds them.
        """
        # pylint: disable=import-error,import-outside-toplevel
        import gpiod
        from gpiod import line

        # pylint: enable=import-error,import-outside-toplevel

        lines = gpiod.request_lines(
            chip,
            consumer='paperroute-knobs',
            config={
                tuple(LINES): gpiod.LineSettings(
                    direction=line.Direction.INPUT,
                    edge_detection=line.Edge.BOTH,
                    bias=line.Bias.PULL_UP,
                    debounce_period=DEBOUNCE,
                    event_clock=line.Clock.MONOTONIC,
                ),
            },
        )
        return cls(lines=lines, value=line.Value)

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
            The changes in the order they happened, timed on the same
            clock as `time.monotonic`; empty when none came within the
            timeout.
        """
        if not self.lines.wait_edge_events(timedelta(seconds=timeout)):
            return []
        return [
            LineEdge(
                line=event.line_offset,
                rising=event.event_type == event.Type.RISING_EDGE,
                at=event.timestamp_ns / NANOSECONDS,
            )
            for event in self.lines.read_edge_events()
        ]

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
        return bool(self.lines.get_value(line) == self.value.ACTIVE)

    def close(self) -> None:
        """Release the lines."""
        self.lines.release()
