"""Tests for matching alerts to the shown pin and ranking them."""

from dataclasses import replace
from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.alert_match import (
    Listed,
    PinFacts,
    Timing,
    alert_banner,
    between_names,
    listed_alerts,
    matched_alerts,
    names_place,
    pin_facts,
    place_name,
    time_line,
    timing_of,
)
from paperroute.core.alert_model import (
    Alert,
    AlertBanner,
    AlertTie,
    Matched,
    Period,
)
from paperroute.core.model import Departure, Mode, Pin, RouteKind
from paperroute.core.patterns import PatternStop, TripPattern
from tests.core.alert_factories import (
    PENRITH_CENTRAL,
    RHODES,
    STRATHFIELD_UPDATE,
    WESTERN,
    XPLORER,
    alert,
    station_pin,
    sydney,
)
from tests.core.factories import departure

SAT_MORNING = sydney(day=24, hour=9, minute=39)
MON_NOON = sydney(day=26, hour=12)
MON_NIGHT = sydney(day=26, hour=22)

PARRAMATTA_CENTRAL = station_pin(
    origin='2150',
    origin_name='Parramatta Station',
    destination='200060',
    destination_name='Central Station',
)
SEVEN_HILLS_BLACKTOWN = station_pin(
    origin='2147',
    origin_name='Seven Hills Station',
    destination='2148',
    destination_name='Blacktown Station',
)
STRATHFIELD_TOWN_HALL = station_pin(
    origin='213510',
    origin_name='Strathfield Station',
    destination='2000',
    destination_name='Town Hall Station',
)
CENTRAL_PIN = station_pin(origin='200060', origin_name='Central Station')
STRATHFIELD_PIN = station_pin(
    origin='213510', origin_name='Strathfield Station',
)
BETWEEN = frozenset({'Burwood', 'Redfern', 'Central'})


def trains(*, route: str = 'T1', mode: Mode = Mode.TRAIN) -> list[Departure]:
    """
    Two departures on one route.

    Parameters
    ----------
    route : str
        Route short name.
    mode : Mode
        Mode.

    Returns
    -------
    list[Departure]
        The departures.
    """
    return [
        departure(
            departs=sydney(day=24, hour=hour),
            route=route,
            mode=mode,
            trip_id=f'trip-{hour}',
        )
        for hour in (10, 11)
    ]


def listed_for(
    *,
    pin: Pin,
    found: Alert,
    now: datetime,
    between: frozenset[str] = frozenset(),
    departures: list[Departure] | None = None,
    lookahead: timedelta | None = None,
) -> tuple[Listed, ...]:
    """
    List one alert for a pin.

    Parameters
    ----------
    pin : Pin
        The pin.
    found : Alert
        The alert.
    now : datetime
        Current time.
    between : frozenset[str]
        Place names between From and To.
    departures : list[Departure] | None
        The pin's departures; trains on T1 by default.
    lookahead : timedelta | None
        List lookahead.

    Returns
    -------
    tuple[Listed, ...]
        What is listed.
    """
    facts = pin_facts(
        pin=pin,
        departures=trains() if departures is None else departures,
        between=between,
    )
    return listed_alerts(
        matched=matched_alerts(alerts=[found], facts=facts),
        now=now,
        lookahead=lookahead,
    )


def test_place_name() -> None:
    """Station suffixes are dropped."""
    assert place_name(name='Central Station') == 'Central'
    assert place_name(name='Olympic Park Station') == 'Olympic Park'
    assert place_name(name='Rhodes Shops') == 'Rhodes Shops'


def test_central_coast_is_not_central() -> None:
    """A capitalised next word other than Station makes another place."""
    names = {'Central'}
    assert not names_place(
        text=(
            'Buses also replace T9 Northern and Central Coast & Newcastle '
            'Line trains.'
        ),
        names=names,
    )
    assert names_place(
        text=(
            'Buses replace trains between Central and Strathfield or '
            'Olympic Park.'
        ),
        names=names,
    )
    assert names_place(text='Station Update - Central', names=names)
    assert names_place(text='Central Station', names=names)


def test_whole_words_only() -> None:
    """Names are found only as whole place names."""
    assert not names_place(
        text='North Strathfield', names={'Strathfield'},
    )
    assert not names_place(text='Burwoods', names={'Burwood'})
    assert names_place(
        text='Station Update - Strathfield', names={'Strathfield'},
    )


def test_route_alert_naming_from_reaches_banner() -> None:
    """A route alert naming the pin's From takes the banner."""
    listed = listed_for(
        pin=PARRAMATTA_CENTRAL, found=WESTERN, now=SAT_MORNING,
    )
    assert len(listed) == 1
    assert listed[0].matched.tie == AlertTie.ROUTE
    assert listed[0].timing.active
    assert listed[0].banner


def test_route_alert_not_naming_pin_is_list_only() -> None:
    """A route alert not naming the pin's places is only listed."""
    listed = listed_for(
        pin=SEVEN_HILLS_BLACKTOWN, found=PENRITH_CENTRAL, now=MON_NIGHT,
    )
    assert len(listed) == 1
    assert not listed[0].banner


def test_station_between_reaches_banner() -> None:
    """A station between From and To counts as a place."""
    assert listed_for(
        pin=STRATHFIELD_TOWN_HALL,
        found=PENRITH_CENTRAL,
        now=MON_NIGHT,
        between=BETWEEN,
    )[0].banner
    assert not listed_for(
        pin=STRATHFIELD_TOWN_HALL, found=PENRITH_CENTRAL, now=MON_NIGHT,
    )[0].banner


def test_bus_pin_never_matches_by_name() -> None:
    """Bus pins don't match alert text against stop names."""
    pin = station_pin(
        origin='213820', origin_name='Rhodes', kind=RouteKind.BUS,
    )
    listed = listed_for(
        pin=pin,
        found=RHODES,
        now=sydney(day=25, hour=6),
        departures=trains(route='526', mode=Mode.BUS),
    )
    assert len(listed) == 1
    assert not listed[0].banner


def test_coming_route_alert_not_in_banner() -> None:
    """A route alert takes the banner only while active."""
    listed = listed_for(
        pin=STRATHFIELD_TOWN_HALL,
        found=PENRITH_CENTRAL,
        now=MON_NOON,
        between=BETWEEN,
    )
    assert len(listed) == 1
    assert not listed[0].banner


def test_stop_alert_reaches_banner() -> None:
    """An active alert about the From stop takes the banner."""
    listed = listed_for(
        pin=CENTRAL_PIN, found=XPLORER, now=sydney(day=24, hour=9),
    )
    assert listed[0].matched.tie == AlertTie.STOP
    assert listed[0].timing.active
    assert listed[0].banner


def test_open_ended_never_banner() -> None:
    """An alert with no end is listed as ongoing, never in the banner."""
    listed = listed_for(
        pin=STRATHFIELD_PIN, found=STRATHFIELD_UPDATE, now=SAT_MORNING,
    )
    assert len(listed) == 1
    assert listed[0].matched.tie == AlertTie.STOP
    assert not listed[0].banner
    assert time_line(timing=listed[0].timing, now=SAT_MORNING) == 'Ongoing'
    platform = station_pin(
        origin='2135231', origin_name='Strathfield Station Platform 1',
    )
    family = replace(
        STRATHFIELD_UPDATE,
        informed=(
            replace(
                STRATHFIELD_UPDATE.informed[0],
                stops=frozenset({'213510', '2135231'}),
            ),
        ),
    )
    assert listed_for(pin=platform, found=family, now=SAT_MORNING)
    coming = replace(
        STRATHFIELD_UPDATE,
        periods=(Period(start=sydney(day=26, hour=21, minute=50)),),
    )
    listed = listed_for(pin=STRATHFIELD_PIN, found=coming, now=SAT_MORNING)
    assert time_line(timing=listed[0].timing, now=SAT_MORNING) == (
        'From Mon 21:50, ongoing'
    )
    assert not listed[0].banner


def test_trip_alert_ranks_first() -> None:
    """An alert naming one of the pin's trips ranks first."""
    rides = [replace(trains()[0], aliases=('alias-1',))]
    trip = alert(
        alert_id='trip',
        header=XPLORER.header,
        periods=XPLORER.periods,
        trip_id='alias-1',
    )
    facts = pin_facts(pin=CENTRAL_PIN, departures=rides, between=frozenset())
    listed = listed_alerts(
        matched=matched_alerts(alerts=[XPLORER, trip], facts=facts),
        now=sydney(day=24, hour=9),
        lookahead=None,
    )
    assert [item.matched.tie for item in listed] == [
        AlertTie.TRIP,
        AlertTie.STOP,
    ]


def test_coming_within_day_reaches_banner() -> None:
    """A stop alert starting within a day takes the outlined banner."""
    now = sydney(day=24, hour=1)
    listed = listed_for(pin=CENTRAL_PIN, found=XPLORER, now=now)
    assert not listed[0].timing.active
    assert listed[0].banner
    assert alert_banner(listed=listed, now=now) == AlertBanner(
        text=(
            'From 6:00 · Saturday 24th October 2026 - 631 Sydney to '
            'Canberra Xplorer service.'
        ),
        active=False,
        more=0,
    )


def test_windows_judged_in_turn() -> None:
    """The current window, else the next, is judged."""
    timing = timing_of(alert=PENRITH_CENTRAL, now=MON_NOON)
    assert timing == Timing(
        window=PENRITH_CENTRAL.periods[0], active=False, more=1,
    )
    timing = timing_of(
        alert=PENRITH_CENTRAL, now=sydney(day=26, hour=23),
    )
    assert timing is not None
    assert timing.active
    assert timing.more == 1
    timing = timing_of(alert=PENRITH_CENTRAL, now=sydney(day=27, hour=3))
    assert timing == Timing(
        window=PENRITH_CENTRAL.periods[1], active=False, more=0,
    )
    late = sydney(day=28, hour=2)
    assert timing_of(alert=PENRITH_CENTRAL, now=late) is None
    assert not listed_for(
        pin=SEVEN_HILLS_BLACKTOWN, found=PENRITH_CENTRAL, now=late,
    )


def test_no_periods_always_active() -> None:
    """An alert with no windows applies now."""
    timing = timing_of(
        alert=replace(XPLORER, periods=()), now=SAT_MORNING,
    )
    assert timing == Timing(window=Period(), active=True, more=0)


def test_open_end_never_ends() -> None:
    """A window with no end is active long after it starts."""
    timing = timing_of(
        alert=STRATHFIELD_UPDATE, now=sydney(month=12, day=25, hour=9),
    )
    assert timing is not None
    assert timing.active


def test_lookahead_limits_list() -> None:
    """Coming alerts are listed only within the lookahead."""
    for days in (3, 7, None):
        assert listed_for(
            pin=SEVEN_HILLS_BLACKTOWN,
            found=PENRITH_CENTRAL,
            now=SAT_MORNING,
            lookahead=None if days is None else timedelta(days=days),
        )
    assert not listed_for(
        pin=SEVEN_HILLS_BLACKTOWN,
        found=PENRITH_CENTRAL,
        now=SAT_MORNING,
        lookahead=timedelta(days=1),
    )


RANKED = (
    Matched(
        alert=replace(XPLORER, alert_id='a'),
        tie=AlertTie.TRIP,
        names_place=False,
    ),
    Matched(
        alert=replace(XPLORER, alert_id='b'),
        tie=AlertTie.STOP,
        names_place=False,
    ),
    Matched(alert=WESTERN, tie=AlertTie.ROUTE, names_place=True),
    Matched(alert=RHODES, tie=AlertTie.STOP, names_place=False),
    Matched(alert=PENRITH_CENTRAL, tie=AlertTie.STOP, names_place=False),
)


@given(order=st.permutations(RANKED))
def test_rank_order(order: list[Matched]) -> None:
    """Active before coming, then by tie, then soonest first."""
    listed = listed_alerts(
        matched=order, now=sydney(day=24, hour=9), lookahead=None,
    )
    assert [item.matched for item in listed] == list(RANKED)


def test_more_counts_banner_eligible() -> None:
    """The banner counts only the other banner-eligible alerts."""
    now = sydney(day=24, hour=9)
    listed = listed_alerts(
        matched=[
            Matched(alert=XPLORER, tie=AlertTie.STOP, names_place=False),
            Matched(
                alert=replace(XPLORER, alert_id='other'),
                tie=AlertTie.STOP,
                names_place=False,
            ),
            Matched(alert=WESTERN, tie=AlertTie.ROUTE, names_place=False),
        ],
        now=now,
        lookahead=None,
    )
    assert alert_banner(listed=listed, now=now) == AlertBanner(
        text=XPLORER.header, active=True, more=1,
    )


def test_no_banner_without_eligible() -> None:
    """With nothing banner-eligible there is no alert banner."""
    listed = listed_for(
        pin=STRATHFIELD_PIN, found=STRATHFIELD_UPDATE, now=SAT_MORNING,
    )
    assert alert_banner(listed=listed, now=SAT_MORNING) is None


def test_time_lines() -> None:
    """Time lines show the judged window and how many follow."""
    timing = timing_of(alert=PENRITH_CENTRAL, now=MON_NOON)
    assert timing is not None
    assert time_line(timing=timing, now=MON_NOON) == (
        '21:50 to Tue 1:30 · +1 more'
    )
    timing = timing_of(alert=WESTERN, now=SAT_MORNING)
    assert timing is not None
    assert time_line(timing=timing, now=SAT_MORNING) == 'Now until Mon 2:00'
    assert time_line(
        timing=Timing(window=Period(), active=True, more=0),
        now=SAT_MORNING,
    ) == 'Ongoing'
    later = Timing(
        window=Period(start=sydney(month=11, day=2, hour=21, minute=50)),
        active=False,
        more=0,
    )
    assert time_line(timing=later, now=SAT_MORNING) == (
        'From Mon 2 Nov 21:50, ongoing'
    )


def named_stop(*, station: str) -> PatternStop:
    """
    A call at a named station.

    Parameters
    ----------
    station : str
        Station ID, also its name with ' Station' added.

    Returns
    -------
    PatternStop
        The call.
    """
    return PatternStop(
        arrives=0,
        departs=0,
        point=None,
        station=station,
        name=f'{station} Station',
    )


def line(*, trip_id: str, stations: str) -> TripPattern:
    """
    A train pattern calling at single-letter stations.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    stations : str
        Station IDs, one letter each, in running order.

    Returns
    -------
    TripPattern
        The pattern.
    """
    return TripPattern(
        trip_id=trip_id,
        route='T1',
        mode=Mode.TRAIN,
        shape_id=None,
        stops=tuple(named_stop(station=station) for station in stations),
        days=(),
    )


def test_between_names() -> None:
    """Stations strictly between From and To are named."""
    pin = station_pin(
        origin='A', origin_name='A', destination='D', destination_name='D',
    )
    patterns = {
        'one': line(trip_id='one', stations='ABCDE'),
        'two': line(trip_id='two', stations='AXY'),
    }
    assert between_names(pin=pin, patterns=patterns) == {'B', 'C'}
    alone = station_pin(origin='A', origin_name='A')
    assert between_names(pin=alone, patterns=patterns) == frozenset()


def test_between_names_from_a_platform() -> None:
    """A From or To on a platform is found through its station."""
    pin = station_pin(
        origin='a1', origin_name='A', destination='d1', destination_name='D',
    )
    patterns = {'one': line(trip_id='one', stations='ABCDE')}
    assert between_names(pin=pin, patterns=patterns) == frozenset()
    families = {
        'a1': frozenset({'a1', 'A'}),
        'd1': frozenset({'d1', 'D'}),
    }
    assert between_names(
        pin=pin, patterns=patterns, families=families,
    ) == {'B', 'C'}


def test_pin_facts_name_pin_by_modes() -> None:
    """A pin of any kind is a name pin when every departure is rail."""
    pin = replace(PARRAMATTA_CENTRAL, kind=None)
    facts = pin_facts(pin=pin, departures=trains(), between=frozenset())
    assert facts == PinFacts(
        trips=frozenset({'trip-10', 'trip-11'}),
        stops=frozenset({'2150', '200060'}),
        routes=frozenset({'T1'}),
        names=frozenset({'Parramatta', 'Central'}),
    )
    mixed = trains() + trains(route='526', mode=Mode.BUS)
    assert not pin_facts(pin=pin, departures=mixed, between=BETWEEN).names


def test_unmatched_alert_is_not_listed() -> None:
    """An alert with no tie to the pin is dropped."""
    assert not listed_for(pin=CENTRAL_PIN, found=RHODES, now=SAT_MORNING)
