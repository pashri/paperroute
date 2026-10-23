"""A Tkinter window standing in for the e-paper panel and knobs."""

import base64
import time
import tkinter
from collections.abc import Callable
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.messages import (
    Event,
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
    QuitRequested,
)
from paperroute.render.canvas import HEIGHT, WIDTH, encode_png

KEYS: Final[dict[str, Event]] = {
    'a': KnobTurned(knob=Knob.TOP, steps=-1),
    'd': KnobTurned(knob=Knob.TOP, steps=1),
    'j': KnobTurned(knob=Knob.BOTTOM, steps=-1),
    'l': KnobTurned(knob=Knob.BOTTOM, steps=1),
    'q': QuitRequested(),
    'Escape': QuitRequested(),
}
HOLD_S: Final[float] = 0.8  # seconds a key is held to count as a hold
REPEAT_GAP_S: Final[float] = 0.05  # seconds between an autorepeat up and down
HOLD_KEYS: Final[dict[str, Knob]] = {'s': Knob.TOP, 'k': Knob.BOTTOM}


class HoldKey:
    """
    Tells a tap, a hold and a turn while held apart on one knob's key.

    X11 autorepeat sends a release then a press for each repeat, so a
    release only counts once no press follows it within `REPEAT_GAP_S`.
    The hold is settled on release: a turn while the key is down makes
    it a turn while held, with no hold after. The caller passes the
    current time and calls `poll` regularly.

    Parameters
    ----------
    knob : Knob
        The knob this key plays.
    """

    def __init__(self, *, knob: Knob) -> None:
        self.knob = knob
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

    def down(self, now: float) -> Event | None:
        """
        Record a key press; a press during autorepeat changes nothing.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        Event | None
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

    def steered(self, event: Event | None) -> Event | None:
        """
        Make this knob's turn a turn while held when the key is down.

        Parameters
        ----------
        event : Event | None
            The event of another key.

        Returns
        -------
        Event | None
            `KnobHeldTurned` for this knob's turn while held, which also
            stops the hold; else the event as it is.
        """
        if self._down_at is None or self._absorbed:
            return event
        if not isinstance(event, KnobTurned) or event.knob is not self.knob:
            return event
        self._turned = True
        return KnobHeldTurned(knob=self.knob, steps=event.steps)

    def poll(self, now: float) -> Event | None:
        """
        Settle a release once the repeat gap has passed.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        Event | None
            `KnobHeld` for a release after `HOLD_S` with no turn,
            `KnobPressed` for a tap, else None.
        """
        down, up = self._down_at, self._up_at
        if down is None or up is None or now - up < REPEAT_GAP_S:
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

    def poll(self, *, now: float) -> Event | None:
        """
        Send `KnobsHeld` once when both keys have stayed down.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        Event | None
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


def key_event(*, keysym: str) -> Event | None:
    """
    The knob event for a key.

    Parameters
    ----------
    keysym : str
        Tk key symbol.

    Returns
    -------
    Event | None
        Event, or None for unmapped keys.
    """
    return KEYS.get(keysym)


def turned_frame(
    *, frame: NDArray[np.uint8], turned: bool
) -> NDArray[np.uint8]:
    """
    Turn a frame upside down, or leave it.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        Grey pixels.
    turned : bool
        Whether to turn it.

    Returns
    -------
    NDArray[np.uint8]
        The frame rotated 180 degrees when turned, else the frame.
    """
    return np.rot90(frame, 2) if turned else frame


class TkMonitor:
    """An 800x480 window showing frames; keys post knob events."""

    def __init__(self) -> None:
        self.post: Callable[[Event], None] | None = None
        self.holds = {
            key: HoldKey(knob=knob) for key, knob in HOLD_KEYS.items()
        }
        self.chord = Chord(
            top=self.holds['s'], bottom=self.holds['k'],
        )
        self.root = tkinter.Tk()
        self.root.title('paperroute')
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.label = tkinter.Label(
            self.root, width=WIDTH, height=HEIGHT, bd=0,
        )
        self.label.pack()
        self.image: tkinter.PhotoImage | None = None

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
    ) -> None:
        """
        Show a frame, upside down when turned.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels.
        full : bool
            Ignored; the window has no ghosting to clear.
        turned : bool
            Whether to show the frame upside down.
        """
        del full
        shown = turned_frame(frame=frame, turned=turned)
        data = base64.b64encode(encode_png(grey=shown)).decode('ascii')
        self.image = tkinter.PhotoImage(data=data, format='png')
        self.label.configure(image=self.image)

    def bind(self, *, post: Callable[[Event], None]) -> None:
        """
        Send knob events from key presses.

        Parameters
        ----------
        post : Callable[[Event], None]
            Receives each event.
        """
        self.post = post
        self.root.bind('<KeyPress>', self.on_key)
        self.root.bind('<KeyRelease>', self.on_key_release)
        self.root.bind('<FocusOut>', self.on_focus_out)

    def on_key(self, key: 'tkinter.Event[tkinter.Misc]') -> None:
        """
        Post the knob event for a key press, if it maps to one.

        Parameters
        ----------
        key : tkinter.Event[tkinter.Misc]
            Tk key event.
        """
        now = time.monotonic()
        hold = self.holds.get(key.keysym.lower())
        if hold is not None:
            event = hold.down(now)
            self.chord.down(now=now)
        else:
            event = key_event(keysym=key.keysym)
            for held in self.holds.values():
                event = held.steered(event)
        if event is not None and self.post is not None:
            self.post(event)

    def on_key_release(self, key: 'tkinter.Event[tkinter.Misc]') -> None:
        """
        Note the release of a hold key.

        Parameters
        ----------
        key : tkinter.Event[tkinter.Misc]
            Tk key event.
        """
        hold = self.holds.get(key.keysym.lower())
        if hold is not None:
            hold.up(time.monotonic())

    def on_focus_out(self, key: 'tkinter.Event[tkinter.Misc]') -> None:
        """
        Drop pending holds when the window loses focus.

        Parameters
        ----------
        key : tkinter.Event[tkinter.Misc]
            Tk focus event.
        """
        del key
        for hold in self.holds.values():
            hold.cancel()

    def on_close(self) -> None:
        """Post QuitRequested when the window's close button is pressed."""
        if self.post is not None:
            self.post(QuitRequested())

    def pump(self) -> None:
        """Process pending window events."""
        self.root.update()
        now = time.monotonic()
        events = [self.chord.poll(now=now)]
        events += [hold.poll(now) for hold in self.holds.values()]
        for event in events:
            if event is not None and self.post is not None:
                self.post(event)

    def close(self) -> None:
        """Close the window; does nothing if it is already gone."""
        try:
            self.root.destroy()
        except tkinter.TclError:
            pass
