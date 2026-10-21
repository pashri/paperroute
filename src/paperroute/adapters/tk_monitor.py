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
    KnobTurned,
    QuitRequested,
)
from paperroute.render.canvas import HEIGHT, WIDTH, encode_png

KEYS: Final[dict[str, Event]] = {
    'a': KnobTurned(knob=Knob.TOP, steps=-1),
    'd': KnobTurned(knob=Knob.TOP, steps=1),
    's': KnobPressed(knob=Knob.TOP),
    'j': KnobTurned(knob=Knob.BOTTOM, steps=-1),
    'l': KnobTurned(knob=Knob.BOTTOM, steps=1),
    'q': QuitRequested(),
    'Escape': QuitRequested(),
}
HOLD_S: Final[float] = 0.8  # seconds a key is held to count as a hold
REPEAT_GAP_S: Final[float] = 0.05  # seconds between an autorepeat up and down
HOLD_KEY: Final[str] = 'k'


class HoldKey:
    """
    Tells a tap, a hold and a turn while held apart on the bottom knob.

    X11 autorepeat sends a release then a press for each repeat, so a
    release only counts once no press follows it within `REPEAT_GAP_S`.
    The hold is settled on release: a turn while the key is down makes
    it a turn while held, with no hold after. The caller passes the
    current time and calls `poll` regularly.
    """

    def __init__(self) -> None:
        self._down_at: float | None = None
        self._up_at: float | None = None
        self._turned = False

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

    def steered(self, event: Event | None) -> Event | None:
        """
        Make a bottom turn a turn while held when the key is down.

        Parameters
        ----------
        event : Event | None
            The event of another key.

        Returns
        -------
        Event | None
            `KnobHeldTurned` for a bottom turn while held, which also
            stops the hold; else the event as it is.
        """
        if self._down_at is None or not isinstance(event, KnobTurned):
            return event
        if event.knob is not Knob.BOTTOM:
            return event
        self._turned = True
        return KnobHeldTurned(knob=Knob.BOTTOM, steps=event.steps)

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
        turned = self._turned
        self.cancel()
        if turned:
            return None
        if up - down >= HOLD_S:
            return KnobHeld(knob=Knob.BOTTOM)
        return KnobPressed(knob=Knob.BOTTOM)


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


class TkMonitor:
    """An 800x480 window showing frames; keys post knob events."""

    def __init__(self) -> None:
        self.post: Callable[[Event], None] | None = None
        self.hold = HoldKey()
        self.root = tkinter.Tk()
        self.root.title('paperroute')
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.label = tkinter.Label(
            self.root, width=WIDTH, height=HEIGHT, bd=0,
        )
        self.label.pack()
        self.image: tkinter.PhotoImage | None = None

    def show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Show a frame.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels.
        """
        data = base64.b64encode(encode_png(grey=frame)).decode('ascii')
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
        if key.keysym.lower() == HOLD_KEY:
            event = self.hold.down(time.monotonic())
        else:
            event = self.hold.steered(key_event(keysym=key.keysym))
        if event is not None and self.post is not None:
            self.post(event)

    def on_key_release(self, key: 'tkinter.Event[tkinter.Misc]') -> None:
        """
        Note the release of the hold key.

        Parameters
        ----------
        key : tkinter.Event[tkinter.Misc]
            Tk key event.
        """
        if key.keysym.lower() == HOLD_KEY:
            self.hold.up(time.monotonic())

    def on_focus_out(self, key: 'tkinter.Event[tkinter.Misc]') -> None:
        """
        Drop a pending hold when the window loses focus.

        Parameters
        ----------
        key : tkinter.Event[tkinter.Misc]
            Tk focus event.
        """
        del key
        self.hold.cancel()

    def on_close(self) -> None:
        """Post QuitRequested when the window's close button is pressed."""
        if self.post is not None:
            self.post(QuitRequested())

    def pump(self) -> None:
        """Process pending window events."""
        self.root.update()
        event = self.hold.poll(time.monotonic())
        if event is not None and self.post is not None:
            self.post(event)

    def close(self) -> None:
        """Close the window; does nothing if it is already gone."""
        try:
            self.root.destroy()
        except tkinter.TclError:
            pass
