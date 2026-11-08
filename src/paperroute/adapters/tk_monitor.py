"""A Tkinter window standing in for the e-paper panel and knobs."""

import base64
import time
import tkinter
from collections.abc import Callable
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import HEIGHT, WIDTH, encode_png
from paperroute_hardware.core.knob_events import Knob, KnobTurned
from paperroute_hardware.core.knob_hold import Chord, HoldKey
from paperroute_hardware.ports import CLEAN_AFTER
from paperroute_transit.core.messages import Event, QuitRequested

KEYS: Final[dict[str, Event]] = {
    'a': KnobTurned(knob=Knob.TOP, steps=-1),
    'd': KnobTurned(knob=Knob.TOP, steps=1),
    'j': KnobTurned(knob=Knob.BOTTOM, steps=-1),
    'l': KnobTurned(knob=Knob.BOTTOM, steps=1),
    'q': QuitRequested(),
    'Escape': QuitRequested(),
}
HOLD_KEYS: Final[dict[str, Knob]] = {'s': Knob.TOP, 'k': Knob.BOTTOM}


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
        quick: bool = False,
        asleep: bool = False,
        clear: bool = False,
        clean_after: int = CLEAN_AFTER,
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
        quick : bool
            Ignored.
        asleep : bool
            Ignored.
        clear : bool
            Ignored.
        clean_after : int
            Ignored.
        """
        del full, quick, asleep, clear, clean_after
        shown = turned_frame(frame=frame, turned=turned)
        data = base64.b64encode(encode_png(grey=shown)).decode('ascii')
        self.image = tkinter.PhotoImage(data=data, format='png')
        self.label.configure(image=self.image)

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
        event: Event | None
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
