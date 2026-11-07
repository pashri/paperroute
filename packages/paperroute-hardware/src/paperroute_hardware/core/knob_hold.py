"""Telling a knob's press, hold and turn while held apart, over time."""

from typing import Final

from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobInput,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)

HOLD_S: Final[float] = 0.8  # seconds a knob is held to count as a hold
REPEAT_GAP_S: Final[float] = 0.05  # seconds between an autorepeat up and down


class HoldKey:
    """
    Tells a tap, a hold and a turn while held apart on one knob's key.

    X11 autorepeat sends a release then a press for each repeat, so a
    release only counts once no press follows it within `gap`. The hold
    is settled on release: a turn while the key is down makes it a turn
    while held, with no hold after. The caller passes the current time
    and calls `poll` regularly.

    Parameters
    ----------
    knob : Knob
        The knob this key plays.
    gap : float
        Seconds a release waits for a repeat press before it counts;
        0 for a real switch, which has no autorepeat.
    """

    def __init__(self, *, knob: Knob, gap: float = REPEAT_GAP_S) -> None:
        self.knob = knob
        self.gap = gap
        self._down_at: float | None = None
        self._up_at: float | None = None
        self._turned = False
        self._absorbed = False

    @property
    def is_down(self) -> bool:
        """
        Whether the key is down and no release has settled.

        Returns
        -------
        bool
            True while the key is held, autorepeat gaps included.
        """
        return self._down_at is not None

    def absorb(self) -> None:
        """Forget the press and ignore the key until its release settles."""
        if self._down_at is not None:
            self._absorbed = True

    def down(self, now: float) -> KnobInput | None:
        """
        Record a key press; a press during autorepeat changes nothing.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        KnobInput | None
            The event for an earlier release this press settles, if any.
        """
        event = self.poll(now)
        if self._down_at is None:
            self._down_at = now
            self._turned = False
        self._up_at = None
        return event

    def up(self, now: float) -> None:
        """
        Record a key release; `poll` decides what it meant.

        Parameters
        ----------
        now : float
            Current time in seconds.
        """
        if self._down_at is not None:
            self._up_at = now

    def cancel(self) -> None:
        """Forget a pending press or hold without posting anything."""
        self._down_at = self._up_at = None
        self._turned = False
        self._absorbed = False

    def steered[E](self, event: E) -> E | KnobHeldTurned:
        """
        Make this knob's turn a turn while held when the key is down.

        Parameters
        ----------
        event : E
            The event of another key, of any kind, or None.

        Returns
        -------
        E | KnobHeldTurned
            `KnobHeldTurned` for this knob's turn while held, which also
            stops the hold; else the event as it is.
        """
        if self._down_at is None or self._absorbed:
            return event
        if not isinstance(event, KnobTurned) or event.knob is not self.knob:
            return event
        self._turned = True
        return KnobHeldTurned(knob=self.knob, steps=event.steps)

    def poll(self, now: float) -> KnobInput | None:
        """
        Settle a release once the repeat gap has passed.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        KnobInput | None
            `KnobHeld` for a release after `HOLD_S` with no turn,
            `KnobPressed` for a tap, else None.
        """
        down, up = self._down_at, self._up_at
        if down is None or up is None or now - up < self.gap:
            return None
        quiet = self._turned or self._absorbed
        self.cancel()
        if quiet:
            return None
        if up - down >= HOLD_S:
            return KnobHeld(knob=self.knob)
        return KnobPressed(knob=self.knob)


class Chord:
    """
    Spots both knob keys held down together for `HOLD_S`.

    When the second key goes down, both keys are absorbed so neither
    sends its own press or hold. A release before the hold time drops
    the chord and sends nothing.

    Parameters
    ----------
    top : HoldKey
        The top knob's key.
    bottom : HoldKey
        The bottom knob's key.
    """

    def __init__(self, *, top: HoldKey, bottom: HoldKey) -> None:
        self.top = top
        self.bottom = bottom
        self._started_at: float | None = None
        self._sent = False

    def down(self, *, now: float) -> None:
        """
        Start the chord if both keys are down; call after a key's down.

        Parameters
        ----------
        now : float
            Current time in seconds.
        """
        if not (self.top.is_down and self.bottom.is_down):
            return
        self.top.absorb()
        self.bottom.absorb()
        if self._started_at is None:
            self._started_at = now

    def poll(self, *, now: float) -> KnobInput | None:
        """
        Send `KnobsHeld` once when both keys have stayed down.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        KnobInput | None
            `KnobsHeld` the first time the hold time passes, else None.
        """
        if self._started_at is None:
            return None
        if not (self.top.is_down and self.bottom.is_down):
            self._started_at = None
            self._sent = False
            return None
        if self._sent or now - self._started_at < HOLD_S:
            return None
        self._sent = True
        return KnobsHeld()
