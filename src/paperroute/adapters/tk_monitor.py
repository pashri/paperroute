"""A Tkinter window standing in for the e-paper panel and knobs."""

import base64
import tkinter
from collections.abc import Callable
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.state import (
    Event,
    Knob,
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
    'k': KnobPressed(knob=Knob.BOTTOM),
    'q': QuitRequested(),
    'Escape': QuitRequested(),
}


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

    def on_key(self, key: 'tkinter.Event[tkinter.Misc]') -> None:
        """
        Post the knob event for a key press, if it maps to one.

        Parameters
        ----------
        key : tkinter.Event[tkinter.Misc]
            Tk key event.
        """
        event = key_event(keysym=key.keysym)
        if event is not None and self.post is not None:
            self.post(event)

    def on_close(self) -> None:
        """Post QuitRequested when the window's close button is pressed."""
        if self.post is not None:
            self.post(QuitRequested())

    def pump(self) -> None:
        """Process pending window events."""
        self.root.update()

    def close(self) -> None:
        """Close the window; does nothing if it is already gone."""
        try:
            self.root.destroy()
        except tkinter.TclError:
            pass
