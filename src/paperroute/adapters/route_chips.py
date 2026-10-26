"""Route chips on the Pins list, and the sheet of their colours."""

import html
from collections.abc import Iterable
from typing import Final

from paperroute.core.chooser import KIND_LETTERS
from paperroute.core.model import Pin, RouteKind
from paperroute.core.route_colours import RouteColour
from paperroute.observers import PinsSeen
from paperroute.ports import PinOptions

CHIP_LETTERS: Final[dict[RouteKind, str]] = {
    **KIND_LETTERS,
    RouteKind.REPLACEMENT: 'R',
}
ANY_TEXT: Final[str] = 'All'
ANY_KIND: Final[str] = 'any'
CHIP: Final[str] = (
    '<span class="chip {classes}" aria-hidden="true">{text}</span>'
)
RULE: Final[str] = (
    '.chip.c-{fill}-{text} {{ background: #{fill}; color: #{text}; }}\n'
)


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
        The first of the pin's route filter, else of its routes; None
        when it has neither.
    """
    return next(iter(pin.only or pin.routes), None)


def chip_html(*, pin: Pin, colour: RouteColour | None) -> str:
    """
    Draw a pin's route chip.

    Parameters
    ----------
    pin : Pin
        The pin.
    colour : RouteColour | None
        The chip route's colours, or None to use the kind's.

    Returns
    -------
    str
        A span hidden from screen readers, its text escaped.
    """
    route = chip_route(pin=pin)
    letter = None if pin.kind is None else CHIP_LETTERS[pin.kind]
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
