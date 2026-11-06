"""Turning the physical knobs' turns and switch edges into knob events."""

from collections.abc import Callable
from dataclasses import replace
from enum import StrEnum
from typing import Final

from paperroute.core.knob_hold import Chord, HoldKey
from paperroute.core.messages import (
    Event,
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobTurned,
)


class Encoder(StrEnum):
    """The two rotary encoders as wired, before either is given a place."""

    A = 'a'
    B = 'b'


PLACES: Final[dict[Encoder, Knob]] = {
    Encoder.A: Knob.TOP,
    Encoder.B: Knob.BOTTOM,
}
OTHER: Final[dict[Knob, Knob]] = {
    Knob.TOP: Knob.BOTTOM,
    Knob.BOTTOM: Knob.TOP,
}


def swapped_event(*, event: Event, swap: bool) -> Event:
    """
    Trade the top and bottom knob in an event.

    Parameters
    ----------
    event : Event
        A knob event.
    swap : bool
        Whether the knobs are swapped.

    Returns
    -------
    Event
        The event with its knob traded when swapped; any event with no
        knob, such as `KnobsHeld`, as it is.
    """
    if not swap:
        return event
    match event:
        case KnobTurned() | KnobPressed() | KnobHeld() | KnobHeldTurned():
            return replace(event, knob=OTHER[event.knob])
    return event


class KnobDecoder:
    """
    Tells turns, presses, holds and both knobs held apart.

    A press is a release within `HOLD_S`, a hold a release after it,
    both with no turn while down. A turn while down is a held turn, and
    the release then sends nothing. Both down together for `HOLD_S` is
    one `KnobsHeld`, with no press or hold for either. The caller passes
    the time with each edge and calls `poll` regularly, so `KnobsHeld`
    is sent while both are still down. Encoder A plays the top knob and
    B the bottom, unless `swapped` says otherwise.

    Parameters
    ----------
    swapped : Callable[[], bool]
        Whether the knobs are swapped; asked at every event.
    """

    def __init__(self, *, swapped: Callable[[], bool]) -> None:
        self.swapped = swapped
        self.holds = {
            encoder: HoldKey(knob=knob, gap=0.0)
            for encoder, knob in PLACES.items()
        }
        self.chord = Chord(
            top=self.holds[Encoder.A], bottom=self.holds[Encoder.B],
        )

    def turned(
        self, *, encoder: Encoder, clockwise: bool, now: float,
    ) -> list[Event]:
        """
        Take one detent of a turn.

        Parameters
        ----------
        encoder : Encoder
            The knob turned.
        clockwise : bool
            Whether it turned clockwise (right).
        now : float
            Current time in seconds.

        Returns
        -------
        list[Event]
            One `KnobTurned`, or `KnobHeldTurned` while the knob is down.
        """
        del now
        event: Event | None = KnobTurned(
            knob=PLACES[encoder], steps=1 if clockwise else -1,
        )
        for hold in self.holds.values():
            event = hold.steered(event)
        return self.placed(events=[event])

    def pressed(self, *, encoder: Encoder, now: float) -> list[Event]:
        """
        Take a knob's switch going down.

        Parameters
        ----------
        encoder : Encoder
            The knob pressed.
        now : float
            Current time in seconds.

        Returns
        -------
        list[Event]
            Nothing; a press is settled on release.
        """
        event = self.holds[encoder].down(now)
        self.chord.down(now=now)
        return self.placed(events=[event])

    def released(self, *, encoder: Encoder, now: float) -> list[Event]:
        """
        Take a knob's switch coming up.

        Parameters
        ----------
        encoder : Encoder
            The knob let go.
        now : float
            Current time in seconds.

        Returns
        -------
        list[Event]
            The press or hold the release settles, if any.
        """
        self.holds[encoder].up(now)
        return self.poll(now=now)

    def poll(self, *, now: float) -> list[Event]:
        """
        Send what time alone settles.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        list[Event]
            `KnobsHeld` once both have been down for `HOLD_S`, and any
            press or hold a release has settled.
        """
        events = [self.chord.poll(now=now)]
        events += [hold.poll(now) for hold in self.holds.values()]
        return self.placed(events=events)

    def placed(self, *, events: list[Event | None]) -> list[Event]:
        """
        Drop the empty results and apply the swap.

        Parameters
        ----------
        events : list[Event | None]
            Results, some None.

        Returns
        -------
        list[Event]
            The events, with top and bottom traded when swapped.
        """
        swap = self.swapped()
        return [
            swapped_event(event=event, swap=swap)
            for event in events
            if event is not None
        ]
