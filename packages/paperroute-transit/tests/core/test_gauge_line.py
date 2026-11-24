"""Tests for laying the gauge out in slots."""

from typing import Final

from paperroute_transit.core.gauge_ends import route_line
from paperroute_transit.core.gauge_line import GaugeLine, GaugeRun, MarkKind
from paperroute_transit.core.names import JOINING_WORDS, NameRules
from paperroute_transit.core.stop_under import GaugeCall
from tests.core.builders import named_pattern

PLAIN: Final[NameRules] = NameRules()
JOINED: Final[NameRules] = NameRules(joining=JOINING_WORDS)


def calls(*names: str) -> tuple[GaugeCall, ...]:
    """Build calls with minutes counting up."""
    return tuple(
        GaugeCall(name=name, minutes=f':{index:02d}')
        for index, name in enumerate(names, start=1)
    )


def numbered(count: int) -> tuple[GaugeCall, ...]:
    """Build `count` calls named Call 1, Call 2 and so on."""
    return calls(*(f'Call {n}' for n in range(1, count + 1)))


def line(
    origin: str,
    found: tuple[GaugeCall, ...],
    *,
    end: int | None = None,
    room: int = 9,
    rules: NameRules = PLAIN,
) -> GaugeLine:
    """Lay out a gauge from a first-stop From, `end` among the calls."""
    names = (origin, *(call.name for call in found))
    pattern = named_pattern(route='6', names=names, days=())
    return route_line(
        pattern=pattern,
        start=0,
        end=None if end is None else end + 1,
        calls=found,
        buses=(),
        budget=0,
        room=room,
        rules=rules,
    )


def kinds(found: GaugeLine) -> list[tuple[int, MarkKind]]:
    """List each mark's slot and kind."""
    return [(mark.slot, mark.kind) for mark in found.marks]


def test_route_line_with_one_call_has_no_arrow() -> None:
    """All calls placed means no arrow."""
    found = line('Hollis St', numbered(1))
    assert kinds(found) == [(0, MarkKind.FROM), (1, MarkKind.CALL)]
    assert not found.arrow


def test_route_line_rides_to_a_destination_in_reach_and_runs_on() -> None:
    """A near Destination is followed by the calls after it."""
    found = line('F', numbered(6), end=2)
    assert kinds(found) == [
        (0, MarkKind.FROM), (1, MarkKind.CALL), (2, MarkKind.CALL),
        (3, MarkKind.DESTINATION), (4, MarkKind.AFTER), (5, MarkKind.AFTER),
        (6, MarkKind.AFTER),
    ]
    assert (found.ride, found.ahead, found.arrow) == ((0, 3), None, False)


def test_route_line_names_a_shared_street_once() -> None:
    """Adjacent stops on one street drop the street from their names."""
    found = line(
        'Norton St At Marion St',
        calls(
            'Parramatta Road Before Catherine St',
            'Parramatta Rd Bef Johnston St',
            'Parramatta Road After Johnston St',
            'Pitt St At Barlow St',
        ),
        rules=JOINED,
    )
    assert found.runs == (GaugeRun(street='Parramatta Rd', first=1, last=3),)
    assert [mark.name for mark in found.marks] == [
        'Norton St At Marion St', 'bef Catherine St', 'bef Johnston St',
        'aft Johnston St', 'Pitt St At Barlow St',
    ]


def test_route_line_joins_the_from_and_destination_to_a_run() -> None:
    """The From and Destination can be part of a run."""
    found = line(
        'Parramatta Rd Opp Univ Of Sydney',
        calls('Parramatta Rd At Ross St'),
        end=0,
        rules=JOINED,
    )
    assert found.runs == (GaugeRun(street='Parramatta Rd', first=0, last=1),)
    assert [mark.name for mark in found.marks] == [
        'opp Univ Of Sydney', 'at Ross St',
    ]


def test_route_line_splits_a_run_at_a_break() -> None:
    """A run stops at a break; the mark after it stands alone."""
    names = (
        'Parramatta Rd At A St', 'Parramatta Rd At B St',
        'Parramatta Rd At C St', *(f'Call {n}' for n in range(4, 10)),
        'Parramatta Rd At J St',
    )
    found = line('Hollis St', calls(*names), end=9, room=6, rules=JOINED)
    assert found.ahead is not None
    assert found.runs == (GaugeRun(street='Parramatta Rd', first=1, last=3),)
    assert found.marks[-1].name == 'Parramatta Rd At J St'


def test_route_line_keeps_whole_names_that_would_read_alike() -> None:
    """Names with equal cross streets stay whole."""
    found = line(
        'Hollis St',
        calls('Victoria Road At Church St', 'Victoria Rd At Church St'),
        rules=JOINED,
    )
    assert found.runs == ()
    assert found.marks[1].name == 'Victoria Road At Church St'
