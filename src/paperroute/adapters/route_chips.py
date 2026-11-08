"""Route chips on the Pins list, and the sheet of their colours."""

import html
from collections.abc import Iterable, Sequence
from typing import Final

from paperroute.observers import PinsSeen
from paperroute.ports import PinOptions
from paperroute_transit.core.model import Pin
from paperroute_transit.core.modes import ModeSpec, spec_of
from paperroute_transit.core.route_colours import RouteColour

ANY_TEXT: Final[str] = 'All'
ANY_KIND: Final[str] = 'any'
CHIP: Final[str] = (
    '<span class="chip {classes}" aria-hidden="true">{text}</span>'
)
RULE: Final[str] = (
    '.chip.c-{fill}-{text} {{ background: #{fill}; color: #{text}; }}\n'
)


def named_routes(*, pin: Pin) -> tuple[str, ...]:
    """
    List the routes a pin's name and chip may claim.

    A pin without a To lists every route of its kind at its stop, so its
    chosen line is not claimed; only its route filter is.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    tuple[str, ...]
        The route filter, else, with a To, the routes chosen.
    """
    if pin.only or pin.destination is None:
        return pin.only
    return pin.routes


def chip_route(*, pin: Pin) -> str | None:
    """
    Pick the route a pin's chip names.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    str | None
        The first of `named_routes`; None when there are none.
    """
    return next(iter(named_routes(pin=pin)), None)


def chip_html(
    *, pin: Pin, colour: RouteColour | None, modes: Sequence[ModeSpec],
) -> str:
    """
    Draw a pin's route chip.

    Parameters
    ----------
    pin : Pin
        The pin.
    colour : RouteColour | None
        The chip route's colours, or None to use the kind's.
    modes : Sequence[ModeSpec]
        The city's modes, whose letters stand in for a route.

    Returns
    -------
    str
        A span hidden from screen readers, its text escaped.
    """
    route = chip_route(pin=pin)
    spec = spec_of(modes=modes, mode=pin.kind)
    letter = None if spec is None else spec.letter
    kind = ANY_KIND if pin.kind is None else pin.kind.replace('_', '-')
    classes = f'mode-{kind}'
    if colour is not None:
        classes += f' c-{colour.fill}-{colour.text}'
    return CHIP.format(
        classes=classes, text=html.escape(route or letter or ANY_TEXT),
    )


def chip_colours(
    *, seen: PinsSeen | None, options: PinOptions | None,
) -> dict[str, RouteColour]:
    """
    Read the colours of the pins' chip routes.

    Parameters
    ----------
    seen : PinsSeen | None
        The pins the app reported, or None before the first report.
    options : PinOptions | None
        The timetable's choices, or None without a timetable.

    Returns
    -------
    dict[str, RouteColour]
        Colours by route name; empty without pins or a timetable.
    """
    if seen is None or options is None:
        return {}
    names = {chip_route(pin=pin) for pin in seen.pins}
    return options.route_colours(
        names={name for name in names if name is not None},
    )


def colour_sheet(*, colours: Iterable[RouteColour]) -> str:
    """
    Write a stylesheet with a rule per colour pair.

    Parameters
    ----------
    colours : Iterable[RouteColour]
        Colours, possibly repeated.

    Returns
    -------
    str
        One rule per distinct pair, sorted; empty for none.
    """
    return ''.join(
        RULE.format(fill=colour.fill, text=colour.text)
        for colour in sorted(set(colours))
    )
