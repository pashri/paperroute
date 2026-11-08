"""Draw the list of pins on the Pins page."""

import html
from collections.abc import Mapping, Sequence
from typing import Final

from paperroute.adapters.route_chips import chip_html, chip_route, named_routes
from paperroute.observers import PinsSeen
from paperroute_transit.core.model import Pin, pin_ref, ticked_ways
from paperroute_transit.core.modes import ModeSpec
from paperroute_transit.core.route_colours import RouteColour

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
        the routes it may claim (see `named_routes`) after a dot; for a
        connection, the From and To, then
        where its ticked ways change.
        Not escaped.
    """
    line = pin.origin.name
    if pin.destination is not None:
        line += f' → {pin.destination.name}'
    routes = named_routes(pin=pin)
    boards = dict.fromkeys(
        change.board.name
        for way in ticked_ways(pin=pin)
        for change in way.changes
    )
    if boards:
        return f'{line} · via {", ".join(boards)}'
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
    *,
    pin: Pin,
    shown: bool,
    colour: RouteColour | None,
    modes: Sequence[ModeSpec],
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
    modes : Sequence[ModeSpec]
        The city's modes.

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
        editor='/pins/edit' if pin.destination is None else '/pins/ways',
        chip=chip_html(pin=pin, colour=colour, modes=modes),
        label=html.escape(pin_label(pin=pin)),
        line=html.escape(pin_line(pin=pin)),
        trip='' if pin.destination is None else ' class="trip"',
        flags=flags,
        up=move_form(pin=pin, steps=-1),
        down=move_form(pin=pin, steps=1),
    )


def pins_list(
    *,
    seen: PinsSeen,
    colours: Mapping[str, RouteColour],
    modes: Sequence[ModeSpec],
) -> str:
    """
    Draw the pins in knob order.

    Parameters
    ----------
    seen : PinsSeen
        The pins the app reported.
    colours : Mapping[str, RouteColour]
        Route colours by short name.
    modes : Sequence[ModeSpec]
        The city's modes.

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
            modes=modes,
        )
        for index, pin in enumerate(seen.pins)
    )
    return f'<ol class="pins">{items}\n    </ol>'
