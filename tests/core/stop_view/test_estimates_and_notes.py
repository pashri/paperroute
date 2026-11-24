"""Tests for stop view: estimates and notes."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE, SydneyFeed, SydneyMode
from paperroute_testkit.core.factories import START, departure
from paperroute_transit.core.city import City, Nouns
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import TripFacts
from paperroute_transit.core.stop_under import UnderNumber
from paperroute_transit.core.stop_view import (
    Estimate,
    Pill,
    Row,
    due_line,
    hero_departure,
    row_estimate,
    to_row,
)
from tests.core.stop_view.helpers import hero, live, on, view


@pytest.mark.parametrize(
    ('late', 'uncertain', 'text'),
    [
        (4, False, 'due 10:07 · 4 min late'),
        (-2, False, 'due 10:07 · 2 min early'),
        (0, False, 'due 10:07 · on time'),
        (31, True, 'due 10:07 · 31 min late, uncertain'),
        (0, True, 'due 10:07 · on time, uncertain'),
    ],
)
def test_due_line_says_how_late_and_whether_uncertain(
    late: int, uncertain: bool, text: str,
) -> None:
    """The due line gives the timetable time, the lateness and the flag."""
    found = Estimate(due='10:07', late=late, uncertain=uncertain)
    assert due_line(estimate=found) == text


def test_row_estimate_counts_lateness_in_clock_minutes() -> None:
    """Leaving 8:07:40 against 8:03 is 4 minutes late, as the count reads."""
    dep = departure(departs=START + timedelta(minutes=3))
    item = LiveDeparture(
        departure=dep,
        departs=dep.departs + timedelta(minutes=4, seconds=40),
        arrives=dep.arrives + timedelta(minutes=4, seconds=40),
        live=True,
        cancelled=False,
        estimated=True,
    )
    row = to_row(
        item=item, now=START, pill=None, zone=SYDNEY_ZONE, city=SYDNEY,
    )
    assert row.countdown == '7'
    assert row.estimate == Estimate(due='8:03', late=4, uncertain=False)


def test_row_estimate_is_none_for_a_feed_time() -> None:
    """A feed's own live time has no due line."""
    dep = departure(departs=START + timedelta(minutes=3))
    item = LiveDeparture(
        departure=dep,
        departs=dep.departs + timedelta(minutes=2),
        arrives=dep.arrives,
        live=True,
        cancelled=False,
    )
    assert row_estimate(item=item, zone=SYDNEY_ZONE) is None


NOTED: Final = replace(
    SYDNEY,
    modes=tuple(
        replace(spec, platform_notes=True)
        if spec.id == SydneyMode.TRAIN
        else spec
        for spec in SYDNEY.modes
    ),
)


def at_platform(
    *, platform: str, feed: SydneyFeed, kind: SydneyMode
) -> LiveDeparture:
    """
    A departure with a platform and a mode.

    Parameters
    ----------
    platform : str
        Platform or stand.
    feed : SydneyFeed
        Feed source.
    kind : SydneyMode
        Mode id.

    Returns
    -------
    LiveDeparture
        Departure.
    """
    item = live(minutes=5, mode=feed, facts=TripFacts(kind=kind))
    return replace(
        item, departure=replace(item.departure, platform=platform),
    )


def row_of(*, item: LiveDeparture, city: City) -> Row:
    """
    Turn a departure into a row.

    Parameters
    ----------
    item : LiveDeparture
        Departure.
    city : City
        The city.

    Returns
    -------
    Row
        Row.
    """
    return to_row(
        item=item, now=START, pill=None, zone=SYDNEY_ZONE, city=city,
    )


def test_a_platform_noted_row_says_its_platform() -> None:
    """A noted mode's row names its platform; an empty one has no note."""
    train = SydneyFeed.TRAIN
    kind = SydneyMode.TRAIN
    noted = at_platform(platform='4', feed=train, kind=kind)
    bare = at_platform(platform='', feed=train, kind=kind)
    assert row_of(item=noted, city=NOTED).note == 'platform 4'
    assert row_of(item=bare, city=NOTED).note == ''


def test_a_noted_row_uses_its_modes_bay_word() -> None:
    """The note opens with the departure's own bay word."""
    words = Nouns(vehicle='train', bay='track', connection='train')
    city = replace(
        NOTED,
        noun_overrides={(SydneyFeed.TRAIN, SydneyMode.TRAIN): words},
    )
    item = at_platform(
        platform='4', feed=SydneyFeed.TRAIN, kind=SydneyMode.TRAIN,
    )
    assert row_of(item=item, city=city).note == 'track 4'


def test_a_mode_without_platform_notes_has_no_note() -> None:
    """Under Sydney, neither a platformed train nor a bus stand notes."""
    train = at_platform(
        platform='4', feed=SydneyFeed.TRAIN, kind=SydneyMode.TRAIN,
    )
    bus = at_platform(
        platform='B', feed=SydneyFeed.BUS, kind=SydneyMode.BUS,
    )
    assert row_of(item=train, city=SYDNEY).note == ''
    assert row_of(item=bus, city=SYDNEY).note == ''


def test_a_late_hero_says_how_late() -> None:
    """2 min late reads as such, and the delay is kept in seconds."""
    result = view(
        departures=(live(minutes=3, late=2), live(minutes=6),
                    live(minutes=9)),
        destination=None,
    )
    assert result.under == UnderNumber(
        status='2 min late', then=('8:06', '8:09'),
    )
    assert result.delay == 120


def test_build_stop_view_gives_the_heros_delay() -> None:
    """A view with a Destination still carries the hero's delay."""
    result = view(
        departures=(live(minutes=3, late=2),), destination='Parramatta',
    )
    assert result.delay == 120
    assert result.under is None


def test_a_cancelled_first_departure_stays_in_the_list_under_the_hero(
) -> None:
    """The hero is the first that runs; the cancelled one is listed."""
    result = view(
        departures=(live(minutes=3, cancelled=True), live(minutes=6),
                    live(minutes=9)),
        destination=None,
    )
    assert hero(result=result).countdown == '6'
    assert [row.pill for row in result.rows] == [Pill.CANCELLED, None]
    assert [row.countdown for row in result.head_rows] == ['6', '3', '9']
    first = hero_departure(view=result)
    assert first is not None
    assert first.departs == START + timedelta(minutes=6)


def test_an_early_uncertain_hero_says_so() -> None:
    """An uncertain estimate one minute early carries both facts."""
    item = replace(
        live(minutes=3, late=-1), estimated=True, uncertain=True,
    )
    result = view(departures=(item,), destination=None)
    assert result.under is not None
    assert result.under.status == '1 min early, uncertain'


def test_an_on_time_hero_says_on_time() -> None:
    """A live departure on its timetable minute is on time."""
    result = view(departures=(live(minutes=3),), destination=None)
    assert result.under is not None
    assert result.under.status == 'on time'
    assert result.delay == 0


def test_a_timetable_hero_says_timetable_time() -> None:
    """Without live data the status says the time is the timetable's."""
    result = view(
        departures=(live(minutes=3, is_live=False),), destination=None,
    )
    assert result.under is not None
    assert result.under.status == 'timetable time'
    assert result.delay == 0


def test_then_lists_the_same_route_and_headsign() -> None:
    """Other routes, other headsigns and cancelled trips are skipped."""
    result = view(
        departures=(
            live(minutes=3),
            on(item=live(minutes=4), route='700'),
            live(minutes=6, cancelled=True),
            live(minutes=9),
            on(item=live(minutes=12), headsign='Westmead'),
            live(minutes=15),
        ),
        destination=None,
    )
    assert result.under is not None
    assert result.under.then == ('8:09', '8:15')


def test_the_heros_platform_is_named_with_its_bay() -> None:
    """A bus on platform B is at 'stand B'."""
    item = live(minutes=3)
    item = replace(item, departure=replace(item.departure, platform='B'))
    result = view(departures=(item,), destination=None)
    assert result.under is not None
    assert result.under.platform == 'stand B'


def test_a_destination_view_has_nothing_under_the_number() -> None:
    """With a Destination nothing changes."""
    result = view(departures=(live(minutes=3),))
    assert result.under is None


def test_build_stop_view_carries_the_city_name_rules() -> None:
    """The view holds the city's name rules."""
    assert view().name_rules == SYDNEY.name_rules


def test_to_row_tidies_the_headsign() -> None:
    """Entities and double spaces in a headsign are cleaned."""
    item = on(item=live(minutes=3), headsign='Addenbrooke&#39;s  Hospital')
    row = to_row(
        item=item, now=START, pill=None, zone=SYDNEY_ZONE, city=SYDNEY,
    )
    assert row.destination == "Addenbrooke's Hospital"


def test_to_row_sets_late_for_a_live_item_only() -> None:
    """A live item carries its feed delay; a timetable item has none."""
    delayed = to_row(
        item=live(minutes=3, late=5), now=START, pill=None,
        zone=SYDNEY_ZONE, city=SYDNEY,
    )
    timetable = to_row(
        item=live(minutes=3, is_live=False), now=START, pill=None,
        zone=SYDNEY_ZONE, city=SYDNEY,
    )
    assert delayed.late == 5
    assert timetable.late is None


def test_to_row_cuts_at_the_dash_for_a_city_that_asks() -> None:
    """A city with the dash rule keeps the part after the dash."""
    city = replace(
        SYDNEY, name_rules=replace(SYDNEY.name_rules, dash=True),
    )
    item = on(item=live(minutes=3), headsign='Acle - Norwich Station')
    assert to_row(
        item=item, now=START, pill=None, zone=SYDNEY_ZONE, city=city,
    ).destination == 'Norwich'
