"""Report the board's settings and pins to its observers."""

from typing import Protocol

from paperroute_transit.core.messages import PinEdit
from paperroute_transit.core.model import Pin
from paperroute_transit.core.saved import Settings
from paperroute_transit.core.state_model import Places, State


class BoardObserver(Protocol):
    """Receives the board's settings and pins."""

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Note the settings in force.

        Parameters
        ----------
        settings : Settings
            The settings.
        """

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """
        Note the pins and which one is on screen.

        Parameters
        ----------
        pins : tuple[Pin, ...]
            The pins.
        shown : int | None
            The pin on screen, or None for none.
        """


def report_pins(
    *, observer: BoardObserver, before: Places, state: State, event: object,
) -> None:
    """
    Report the pins when the places changed or a pin was edited.

    An edit the reducer refused is reported too, so a page waiting on
    it is released.

    Parameters
    ----------
    observer : BoardObserver
        Receives the pins.
    before : Places
        The places before the event.
    state : State
        State after the event.
    event : object
        The event handled.
    """
    places = state.places
    if places != before or isinstance(event, PinEdit):
        pins_shown(observer=observer, places=places)


def pins_shown(*, observer: BoardObserver, places: Places) -> None:
    """
    Report the pins and which one is on screen.

    Parameters
    ----------
    observer : BoardObserver
        Receives the pins.
    places : Places
        The pins and which place is shown.
    """
    on_screen = places.unpinned is None and bool(places.pins)
    observer.pins_seen(
        pins=places.pins,
        shown=places.current if on_screen else None,
    )
