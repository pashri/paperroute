"""Tests for the route chips on the Pins list."""

from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_transit.adapters.route_chips import chip_html, colour_sheet
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.route_colours import RouteColour

STOP: StopPlace = StopPlace(stop_id='a', name='A Stop', point=None)
BLUE: RouteColour = RouteColour(fill='00b5ef', text='000000')


def chip_of(*, pin: Pin, colour: RouteColour | None = None) -> str:
    """
    Draw a pin's chip.

    Parameters
    ----------
    pin : Pin
        The pin.
    colour : RouteColour | None
        The chip route's colours.

    Returns
    -------
    str
        The chip's markup.
    """
    return chip_html(pin=pin, colour=colour, modes=SYDNEY.modes)


def test_chip_text() -> None:
    """The first filtered route, then the kind's letter, then All."""
    routes = Pin(origin=STOP, destination=STOP, routes=('606', '607'))
    only = Pin(origin=STOP, routes=('T1', 'T2'), only=('T2',))
    train = Pin(origin=STOP, kind=SydneyMode.TRAIN)
    assert '>606</span>' in chip_of(pin=routes)
    assert '>T2</span>' in chip_of(pin=only)
    assert '>T</span>' in chip_of(pin=train)
    assert '>All</span>' in chip_of(pin=Pin(origin=STOP))


def test_chip_of_a_pin_without_a_to_names_no_line_it_does_not_filter() -> None:
    """A T1 pin without a To lists every line, so its chip says T."""
    line = Pin(origin=STOP, kind=SydneyMode.TRAIN, routes=('T1',))
    assert '>T</span>' in chip_of(pin=line)


def test_chip_classes() -> None:
    """The kind's class, and the colour class when there is one."""
    bus = Pin(
        origin=STOP, destination=STOP, kind=SydneyMode.BUS, routes=('606',),
    )
    light = Pin(
        origin=STOP, destination=STOP, kind=SydneyMode.LIGHT_RAIL,
        routes=('L1',),
    )
    found = chip_of(pin=bus, colour=BLUE)
    assert 'class="chip mode-bus c-00b5ef-000000"' in found
    assert 'aria-hidden="true"' in found
    assert 'class="chip mode-light-rail"' in chip_of(pin=light)
    assert 'class="chip mode-any"' in chip_of(pin=Pin(origin=STOP))


def test_chip_escapes_text() -> None:
    """A route name is escaped."""
    found = chip_of(pin=Pin(origin=STOP, only=('<b>',)))
    assert '&lt;b&gt;' in found
    assert '<b>' not in found


def test_sheet_has_only_valid_rules() -> None:
    """Equal colours give one rule; no colours give an empty sheet."""
    rule = '.chip.c-00b5ef-000000 { background: #00b5ef; color: #000000; }'
    assert colour_sheet(colours=[BLUE, BLUE]) == f'{rule}\n'
    assert colour_sheet(colours=[]) == ''


def test_every_kind_has_a_letter() -> None:
    """Each kind's chip without routes shows a letter."""
    letters = {
        kind: chip_of(pin=Pin(origin=STOP, kind=kind)) for kind in SydneyMode
    }
    assert '>R</span>' in letters[SydneyMode.REPLACEMENT]
    assert '>S</span>' in letters[SydneyMode.SCHOOL]
