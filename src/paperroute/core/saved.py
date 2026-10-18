"""The Live views, and what the device keeps between restarts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from paperroute.core.geo import to_metres
from paperroute.core.map_view import DEFAULT_ZOOM
from paperroute.core.model import Pin, RouteKind, StopPlace


class LiveView(StrEnum):
    """The views the bottom knob press switches between."""

    STOP = 'stop'
    MAP = 'map'
    STRIP = 'strip'
    STRIP_MAP = 'strip_map'


LIVE_VIEWS: Final[tuple[LiveView, ...]] = (
    LiveView.STOP,
    LiveView.MAP,
    LiveView.STRIP,
    LiveView.STRIP_MAP,
)


@dataclass(frozen=True)
class Saved:
    """
    The pins, the place shown, the Live view and the zoom.

    Attributes
    ----------
    pins : tuple[Pin, ...]
        The pins, in knob order.
    current : int
        Index of the current pin; 0 when there are none.
    unpinned : Pin | None
        The place shown ahead of the pins, or None to show the current pin.
    view : LiveView
        The Live view shown.
    zoom : int
        The map zoom, an index into `ZOOMS`.
    """

    pins: tuple[Pin, ...]
    current: int
    unpinned: Pin | None
    view: LiveView
    zoom: int


TOWN_HALL: Final[StopPlace] = StopPlace(
    stop_id='200070',
    name='Town Hall Station',
    point=to_metres(lon=151.206899293, lat=-33.8735963257),
)
DEFAULT_PLACE: Final[Pin] = Pin(origin=TOWN_HALL, kind=RouteKind.TRAIN)
DEFAULT_SAVED: Final[Saved] = Saved(
    pins=(),
    current=0,
    unpinned=DEFAULT_PLACE,
    view=LiveView.MAP,
    zoom=DEFAULT_ZOOM,
)


def seeded(*, pin: Pin) -> Saved:
    """
    Start with one pin, selected, on the Stop view.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    Saved
        The pin alone, current, with no unpinned place and the default
        zoom.
    """
    return Saved(
        pins=(pin,),
        current=0,
        unpinned=None,
        view=LiveView.STOP,
        zoom=DEFAULT_ZOOM,
    )
