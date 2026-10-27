"""Draw the list of pins on the Pins page."""

import html
from collections.abc import Mapping
from typing import Final

from paperroute.adapters.route_chips import chip_html, chip_route
from paperroute.core.connection_model import first_change
from paperroute.core.model import Pin, is_connection, pin_ref
from paperroute.core.route_colours import RouteColour
from paperroute.observers import PinsSeen

EMPTY: Final[str] = (
    '<p>No pins yet. Search for a stop or station to add one.</p>'
)
NOW: Final[str] = '<span class="now">On the screen now</span>'
MISSING: Final[str] = (
    '<span class="missing">Stop not in the new timetable</span>'
)
ITEM: Final[str] = """
      <li data-ref="{ref}">
        {chip}
        <div class="pin">
          <b>{label}</b>
          <small{trip}>{line}</small>{flags}
        </div>
{up}
{down}
        <a class="edit" href="{editor}?ref={ref}"
           aria-label="Edit {label}">Edit</a>
        <span class="grip" aria-hidden="true">⋮⋮</span>
      </li>"""
MOVE: Final[str] = """        <form class="move" method="post"
              action="/pins/move">
          <input type="hidden" name="ref" value="{ref}">
          <input type="hidden" name="steps" value="{steps}">
          <button type="submit" class="secondary"
                  aria-label="Move {label} {name}">{arrow}</button>
        </form>"""


def pin_label(*, pin: Pin) -> str:
    """
    Name a pin for the list.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    str
        The nickname, else the From name; not escaped.
    """
    return pin.nickname or pin.origin.name


def pin_line(*, pin: Pin) -> str:
    """
    Describe where a pin goes.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    str
        The From, then the To after an arrow when there is one, then
        the routes after a dot; for a connection, the From and To, then
        where it changes.
        Not escaped.
    """
    line = pin.origin.name
    if pin.destination is not None:
        line += f' → {pin.destination.name}'
    routes = pin.only or pin.routes
    change = first_change(pin=pin)
    if change is not None:
        return f'{line} · via {change.board.name}'
    return f'{line} · {" ".join(routes)}' if routes else line


def move_form(*, pin: Pin, steps: int) -> str:
    """
    Draw the Up or Down button of a pin.

    Parameters
    ----------
    pin : Pin
        The pin.
    steps : int
        -1 for Up, 1 for Down.

    Returns
    -------
    str
        A form that posts the move.
    """
    up = steps < 0
    return MOVE.format(
        ref=pin_ref(pin=pin),
        steps=steps,
        label=html.escape(pin_label(pin=pin)),
        name='up' if up else 'down',
        arrow='↑' if up else '↓',
    )


def pin_item(
    *, pin: Pin, shown: bool, colour: RouteColour | None,
) -> str:
    """
    Draw one pin of the list.

    Parameters
    ----------
    pin : Pin
        The pin.
    shown : bool
        Whether the pin is on the screen now.
    colour : RouteColour | None
        The colours of the pin's chip route, if the timetable has them.

    Returns
    -------
    str
        An escaped list item.
    """
    flags = ''.join(
        f'\n          {flag}'
        for flag, on in ((NOW, shown), (MISSING, pin.missing))
        if on
    )
    return ITEM.format(
        ref=pin_ref(pin=pin),
        editor='/pins/edit' if not is_connection(pin=pin) else '/pins/connect',
        chip=chip_html(pin=pin, colour=colour),
        label=html.escape(pin_label(pin=pin)),
        line=html.escape(pin_line(pin=pin)),
        trip='' if not is_connection(pin=pin) else ' class="trip"',
        flags=flags,
        up=move_form(pin=pin, steps=-1),
        down=move_form(pin=pin, steps=1),
    )


def pins_list(
    *, seen: PinsSeen, colours: Mapping[str, RouteColour],
) -> str:
    """
    Draw the pins in knob order.

    Parameters
    ----------
    seen : PinsSeen
        The pins the app reported.
    colours : Mapping[str, RouteColour]
        Route colours by short name.

    Returns
    -------
    str
        An escaped ordered list, or a line saying there are none.
    """
    if not seen.pins:
        return EMPTY
    items = ''.join(
        pin_item(
            pin=pin,
            shown=index == seen.shown,
            colour=colours.get(chip_route(pin=pin) or ''),
        )
        for index, pin in enumerate(seen.pins)
    )
    return f'<ol class="pins">{items}\n    </ol>'
