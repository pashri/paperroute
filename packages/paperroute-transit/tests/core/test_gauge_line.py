"""Tests for laying the gauge out in slots."""

from typing import Final

from paperroute_transit.core.gauge_line import (
    GaugeBreak,
    GaugeLine,
    GaugeMark,
    GaugeRun,
    MarkKind,
    gauge_line,
)
from paperroute_transit.core.names import JOINING_WORDS, NameRules
from paperroute_transit.core.stop_under import GaugeCall, StopGauge

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
    gauge: StopGauge,
    *,
    reach: int = 9,
    dest_reach: int = 9,
    rules: NameRules = PLAIN,
) -> GaugeLine:
    """Lay out a gauge."""
    return gauge_line(
        gauge=gauge, reach=reach, dest_reach=dest_reach, rules=rules,
    )


def kinds(found: GaugeLine) -> list[tuple[int, MarkKind]]:
    """List each mark's slot and kind."""
    return [(mark.slot, mark.kind) for mark in found.marks]


def test_gauge_line_places_calls_up_to_the_reach() -> None:
    """Calls beyond the reach are left off and an arrow shows."""
    found = line(StopGauge(origin='Hollis St', calls=numbered(5)), reach=3)
    assert found.marks == (
        GaugeMark(slot=0, kind=MarkKind.FROM, name='Hollis St'),
        GaugeMark(slot=1, kind=MarkKind.CALL, name='Call 1', minutes=':01'),
        GaugeMark(slot=2, kind=MarkKind.CALL, name='Call 2', minutes=':02'),
        GaugeMark(slot=3, kind=MarkKind.CALL, name='Call 3', minutes=':03'),
    )
    assert found.arrow
    assert found.ride is None


def test_gauge_line_with_one_call_has_no_arrow() -> None:
    """All calls placed means no arrow."""
    found = line(StopGauge(origin='Hollis St', calls=numbered(1)))
    assert kinds(found) == [(0, MarkKind.FROM), (1, MarkKind.CALL)]
    assert not found.arrow


def test_gauge_line_without_room_shows_the_from_and_an_arrow() -> None:
    """A reach of zero leaves only the From."""
    found = line(StopGauge(origin='Hollis St', calls=numbered(3)), reach=0)
    assert kinds(found) == [(0, MarkKind.FROM)]
    assert found.arrow


def test_gauge_line_rides_to_a_destination_in_reach_and_runs_on() -> None:
    """A near Destination is followed by the calls after it."""
    found = line(StopGauge(origin='F', calls=numbered(6), end=2))
    assert kinds(found) == [
        (0, MarkKind.FROM), (1, MarkKind.CALL), (2, MarkKind.CALL),
        (3, MarkKind.DESTINATION), (4, MarkKind.AFTER), (5, MarkKind.AFTER),
        (6, MarkKind.AFTER),
    ]
    assert (found.ride, found.ahead, found.arrow) == ((0, 3), None, False)


def test_gauge_line_breaks_two_slots_before_a_far_destination() -> None:
    """A far Destination sits after a break."""
    found = line(
        StopGauge(origin='F', calls=numbered(12), end=10),
        reach=12, dest_reach=6,
    )
    assert kinds(found) == [
        (0, MarkKind.FROM), (1, MarkKind.CALL), (2, MarkKind.CALL),
        (3, MarkKind.CALL), (6, MarkKind.DESTINATION), (7, MarkKind.AFTER),
    ]
    assert found.ahead == GaugeBreak(first=3, last=6, count=7)
    assert (found.ride, found.arrow) == ((0, 6), False)


def test_gauge_line_break_may_keep_no_call() -> None:
    """The break can start at the From."""
    found = line(
        StopGauge(origin='F', calls=numbered(8), end=5),
        reach=12, dest_reach=2,
    )
    assert found.ahead == GaugeBreak(first=0, last=3, count=5)
    assert kinds(found)[1] == (3, MarkKind.DESTINATION)


def test_gauge_line_puts_the_from_three_slots_after_a_far_bus() -> None:
    """A back break takes two empty slots."""
    found = line(StopGauge(
        origin='F', behind=('Stop 2',), hidden=5, bus=0.5,
        calls=numbered(2),
    ))
    assert kinds(found) == [
        (0, MarkKind.BEHIND), (3, MarkKind.FROM), (4, MarkKind.CALL),
        (5, MarkKind.CALL),
    ]
    assert (found.back, found.bus) == (
        GaugeBreak(first=0, last=3, count=5), 0.5,
    )


def test_gauge_line_names_a_shared_street_once() -> None:
    """Adjacent stops on one street drop the street from their names."""
    found = line(StopGauge(
        origin='Norton St At Marion St',
        calls=calls(
            'Parramatta Road Before Catherine St',
            'Parramatta Rd Bef Johnston St',
            'Parramatta Road After Johnston St',
            'Pitt St At Barlow St',
        ),
    ), rules=JOINED)
    assert found.runs == (GaugeRun(street='Parramatta Rd', first=1, last=3),)
    assert [mark.name for mark in found.marks] == [
        'Norton St At Marion St', 'bef Catherine St', 'bef Johnston St',
        'aft Johnston St', 'Pitt St At Barlow St',
    ]


def test_gauge_line_joins_the_from_and_destination_to_a_run() -> None:
    """The From and Destination can be part of a run."""
    found = line(StopGauge(
        origin='Parramatta Rd Opp Univ Of Sydney',
        calls=calls('Parramatta Rd At Ross St'), end=0,
    ), rules=JOINED)
    assert found.runs == (GaugeRun(street='Parramatta Rd', first=0, last=1),)
    assert [mark.name for mark in found.marks] == [
        'opp Univ Of Sydney', 'at Ross St',
    ]


def test_gauge_line_splits_a_run_at_a_break() -> None:
    """Marks either side of a break are separate runs."""
    names = (
        'Parramatta Rd At A St', 'Parramatta Rd At B St',
        'Parramatta Rd At C St', *(f'Call {n}' for n in range(4, 10)),
        'Parramatta Rd At J St', 'Parramatta Rd At K St',
    )
    found = line(
        StopGauge(origin='Hollis St', calls=calls(*names), end=9),
        reach=12, dest_reach=6, rules=JOINED,
    )
    assert found.runs == (
        GaugeRun(street='Parramatta Rd', first=1, last=3),
        GaugeRun(street='Parramatta Rd', first=6, last=7),
    )


def test_gauge_line_keeps_whole_names_that_would_read_alike() -> None:
    """Names with equal cross streets stay whole."""
    found = line(StopGauge(
        origin='Hollis St',
        calls=calls('Victoria Road At Church St', 'Victoria Rd At Church St'),
    ), rules=JOINED)
    assert found.runs == ()
    assert found.marks[1].name == 'Victoria Road At Church St'
