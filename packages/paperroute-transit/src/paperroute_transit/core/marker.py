"""The vehicle marker the Map and Strip views draw."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Marker:
    """One vehicle as drawn.

    Attributes
    ----------
    route : str
        Route label.
    x : float
        Map pixels from the left.
    y : float
        Map pixels from the top.
    bearing : float | None
        Heading in degrees, or None for no wedge.
    live : bool
        True at a fresh reported position, False at a timetable position.
    pinned : bool
        True when the route is one of the pin's.
    age : float | None
        Seconds since a stale vehicle was last heard, floored to whole
        minutes; None for a fresh vehicle or a trip that never reported.
    ends : bool
        True for a count of buses going both ways; it has a wedge at
        each end, at `bearing` and opposite it.
    """

    route: str
    x: float
    y: float
    bearing: float | None
    live: bool
    pinned: bool
    age: float | None = None
    ends: bool = False
