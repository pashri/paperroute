"""Which alerts concern the shown pin, in what order, and when."""

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from paperroute.core.alert_model import (
    Alert,
    AlertBanner,
    AlertTie,
    Matched,
    Period,
)
from paperroute.core.model import SYDNEY, Departure, Mode, Pin, RouteKind
from paperroute.core.patterns import TripPattern
from paperroute.core.stop_view import DOT, clock_text

BANNER_AHEAD: Final[timedelta] = timedelta(hours=24)
NAME_SUFFIXES: Final[tuple[str, ...]] = (
    ' Light Rail Station',
    ' Metro Station',
    ' Light Rail',
    ' Station',
    ' Wharf',
)
NAME_KINDS: Final[frozenset[RouteKind]] = frozenset({
    RouteKind.TRAIN,
    RouteKind.METRO,
    RouteKind.LIGHT_RAIL,
    RouteKind.FERRY,
})
NAME_MODES: Final[frozenset[Mode]] = frozenset({
    Mode.TRAIN,
    Mode.METRO,
    Mode.LIGHT_RAIL,
    Mode.REGIONAL,
    Mode.FERRY,
})
BETWEEN_TRIPS: Final[int] = 12
WEEK_DAYS: Final[int] = 6
NAME_BEFORE: Final[str] = (
    r'(?<![\w-])(?<!North )(?<!South )(?<!East )(?<!West )'
)
NAME_AFTER: Final[str] = r'(?![\w-])(?! +(?!Station\b)[A-Z])'
WHARF_AFTER: Final[str] = r'(?![\w-])(?! +(?!Station\b|Wharf\b)[A-Z])'


@dataclass(frozen=True)
class PinFacts:
    """
    What alerts can name about the shown pin.

    Attributes
    ----------
    trips : frozenset[str]
        Trip IDs and aliases of its departures.
    stops : frozenset[str]
        From and To stop IDs.
    routes : frozenset[str]
        Route short names.
    names : frozenset[str]
        Place names of From, To and the stations between; empty for a
        pin whose places aren't matched by name.
    wharf : bool
        True for a ferry pin, whose names may be followed by 'Wharf'.
    """

    trips: frozenset[str]
    stops: frozenset[str]
    routes: frozenset[str]
    names: frozenset[str]
    wharf: bool = False


@dataclass(frozen=True)
class Timing:
    """
    The window an alert is judged by.

    Attributes
    ----------
    window : Period
        The current window, else the next one.
    active : bool
        True when that window applies now.
    more : int
        How many windows follow it.
    """

    window: Period
    active: bool
    more: int


@dataclass(frozen=True)
class Listed:
    """
    An alert shown in the list.

    Attributes
    ----------
    matched : Matched
        The alert and its tie to the pin.
    timing : Timing
        Its judged window.
    banner : bool
        True when it may take the banner.
    """

    matched: Matched
    timing: Timing
    banner: bool


def place_name(*, name: str) -> str:
    """
    A stop name without its station or wharf suffix.

    A wharf name keeps only the part before its first comma, so
    'Circular Quay, Wharf 2, Side A' is 'Circular Quay'.

    Parameters
    ----------
    name : str
        Stop name, such as 'Central Station'.

    Returns
    -------
    str
        Place name, such as 'Central'.
    """
    if 'Wharf' in name:
        name = name.partition(',')[0]
    for suffix in NAME_SUFFIXES:
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    return name


def names_place(
    *, text: str, names: Collection[str], wharf: bool = False,
) -> bool:
    """
    Say whether text names one of the places.

    A name counts only as whole words, not before another capitalised
    word other than 'Station', or 'Wharf' when `wharf` is set ('Central
    Coast' is not 'Central'), and not after a compass word ('North
    Strathfield' is not 'Strathfield').

    Parameters
    ----------
    text : str
        Alert text.
    names : Collection[str]
        Place names.
    wharf : bool
        True to let 'Wharf' follow a name.

    Returns
    -------
    bool
        True when one is found.
    """
    after = WHARF_AFTER if wharf else NAME_AFTER
    return any(
        re.search(
            pattern=f'{NAME_BEFORE}{re.escape(name)}{after}',
            string=text,
        )
        for name in names
        if name
    )


def is_name_pin(*, pin: Pin, departures: Sequence[Departure]) -> bool:
    """
    Say whether a pin's places are matched against alert text.

    Parameters
    ----------
    pin : Pin
        The pin.
    departures : Sequence[Departure]
        Its departures.

    Returns
    -------
    bool
        True for rail, metro, light rail and ferry pins.
    """
    if pin.kind is not None:
        return pin.kind in NAME_KINDS
    return bool(departures) and all(
        item.mode in NAME_MODES for item in departures
    )


def is_ferry_pin(*, pin: Pin, departures: Sequence[Departure]) -> bool:
    """
    Say whether a pin is a ferry pin.

    Parameters
    ----------
    pin : Pin
        The pin.
    departures : Sequence[Departure]
        Its departures.

    Returns
    -------
    bool
        True for a ferry kind, or for no kind and only ferry departures.
    """
    if pin.kind is not None:
        return pin.kind == RouteKind.FERRY
    return bool(departures) and all(
        item.mode == Mode.FERRY for item in departures
    )


def pin_names(*, pin: Pin, between: frozenset[str]) -> frozenset[str]:
    """
    Place names of a pin's From, To and the stations between.

    Parameters
    ----------
    pin : Pin
        The pin.
    between : frozenset[str]
        Place names of stations between From and To.

    Returns
    -------
    frozenset[str]
        The names.
    """
    places = [pin.origin] + ([] if pin.destination is None else [
        pin.destination,
    ])
    return between | {place_name(name=place.name) for place in places}


def pin_facts(
    *,
    pin: Pin,
    departures: Sequence[Departure],
    between: frozenset[str],
    families: Mapping[str, frozenset[str]] | None = None,
) -> PinFacts:
    """
    Gather what alerts can name about a pin.

    Parameters
    ----------
    pin : Pin
        The shown pin.
    departures : Sequence[Departure]
        Its loaded departures; may be empty.
    between : frozenset[str]
        Place names of stations between From and To.
    families : Mapping[str, frozenset[str]] | None
        Stop families of From and To, so a platform pin also matches its
        station and sibling platforms; None for none known.

    Returns
    -------
    PinFacts
        The facts; routes come from the pin's chosen routes and filter
        as well as from its departures.
    """
    ends = [pin.origin] + ([] if pin.destination is None else [
        pin.destination,
    ])
    named = is_name_pin(pin=pin, departures=departures)
    known = families or {}
    return PinFacts(
        trips=frozenset(
            trip
            for item in departures
            for trip in (item.trip_id, *item.aliases)
        ),
        stops=frozenset(
            stop
            for end in ends
            for stop in known.get(end.stop_id, {end.stop_id})
        ),
        routes=(
            frozenset((*pin.routes, *pin.only))
            | {item.route for item in departures}
        ),
        names=pin_names(pin=pin, between=between) if named else frozenset(),
        wharf=is_ferry_pin(pin=pin, departures=departures),
    )


def between_names(
    *,
    pin: Pin,
    patterns: Mapping[str, TripPattern],
    families: Mapping[str, frozenset[str]] | None = None,
) -> frozenset[str]:
    """
    Place names of stations strictly between a pin's From and To.

    Parameters
    ----------
    pin : Pin
        The pin.
    patterns : Mapping[str, TripPattern]
        Patterns of some of its trips.
    families : Mapping[str, frozenset[str]] | None
        Stop families of From and To. A pin end on a platform is found
        through its parent station here; an end with no family must be
        a station itself.

    Returns
    -------
    frozenset[str]
        The names; empty with no To.
    """
    if pin.destination is None:
        return frozenset()
    known = families or {}
    ends = [
        known.get(place.stop_id, frozenset({place.stop_id}))
        for place in (pin.origin, pin.destination)
    ]
    names: set[str] = set()
    for pattern in patterns.values():
        span = end_indexes(pattern=pattern, start=ends[0], end=ends[1])
        if span is not None:
            names.update(
                place_name(name=stop.name)
                for stop in pattern.stops[span[0] + 1:span[1]]
            )
    return frozenset(names)


def end_indexes(
    *, pattern: TripPattern, start: frozenset[str], end: frozenset[str],
) -> tuple[int, int] | None:
    """
    Find where a trip calls at one station and later at another.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    start : frozenset[str]
        IDs the first station may have.
    end : frozenset[str]
        IDs the later station may have.

    Returns
    -------
    tuple[int, int] | None
        Indexes of the first call at `start` and the next call at `end`
        after it, or None when the trip doesn't make both.
    """
    stations = [stop.station for stop in pattern.stops]
    first = next(
        (index for index, station in enumerate(stations) if station in start),
        None,
    )
    if first is None:
        return None
    later = next(
        (
            index
            for index, station in enumerate(stations)
            if index > first and station in end
        ),
        None,
    )
    return None if later is None else (first, later)


def tie_of(*, alert: Alert, facts: PinFacts) -> AlertTie | None:
    """
    How an alert is tied to a pin.

    Parameters
    ----------
    alert : Alert
        The alert.
    facts : PinFacts
        What it can name about the pin.

    Returns
    -------
    AlertTie | None
        The closest tie, or None when it isn't about the pin.
    """
    informed = alert.informed
    if any(item.trip_id in facts.trips for item in informed):
        return AlertTie.TRIP
    if any(item.stops & facts.stops for item in informed):
        return AlertTie.STOP
    if any(item.route in facts.routes for item in informed):
        return AlertTie.ROUTE
    return None


def matched_alerts(
    *, alerts: Iterable[Alert], facts: PinFacts,
) -> tuple[Matched, ...]:
    """
    The alerts tied to a pin.

    Parameters
    ----------
    alerts : Iterable[Alert]
        Alerts of the pin's feeds.
    facts : PinFacts
        What they can name about the pin.

    Returns
    -------
    tuple[Matched, ...]
        The tied alerts, in the given order.
    """
    found = []
    for alert in alerts:
        tie = tie_of(alert=alert, facts=facts)
        if tie is None:
            continue
        named = tie == AlertTie.ROUTE and names_place(
            text=f'{alert.header}\n{alert.description}',
            names=facts.names,
            wharf=facts.wharf,
        )
        found.append(Matched(alert=alert, tie=tie, names_place=named))
    return tuple(found)


def window_key(period: Period) -> tuple[bool, datetime]:
    """
    Sort key putting windows with no start first, then by start.

    Parameters
    ----------
    period : Period
        Window.

    Returns
    -------
    tuple[bool, datetime]
        The key.
    """
    if period.start is None:
        return (False, datetime.min.replace(tzinfo=UTC))
    return (True, period.start)


def timing_of(*, alert: Alert, now: datetime) -> Timing | None:
    """
    Judge an alert by its current window, else its next one.

    Parameters
    ----------
    alert : Alert
        The alert.
    now : datetime
        Current time.

    Returns
    -------
    Timing | None
        Its timing, or None when every window has ended.
    """
    if not alert.periods:
        return Timing(window=Period(), active=True, more=0)
    windows = sorted(alert.periods, key=window_key)
    for index, window in enumerate(windows):
        if window.end is not None and window.end <= now:
            continue
        active = window.start is None or window.start <= now
        return Timing(
            window=window, active=active, more=len(windows) - index - 1,
        )
    return None


def is_banner(*, matched: Matched, timing: Timing, now: datetime) -> bool:
    """
    Say whether a listed alert may take the banner.

    Parameters
    ----------
    matched : Matched
        The alert and its tie.
    timing : Timing
        Its judged window.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when it may.
    """
    if timing.window.end is None:
        return False
    if matched.tie == AlertTie.ROUTE:
        return timing.active and matched.names_place
    start = timing.window.start
    return timing.active or (
        start is not None and start - now <= BANNER_AHEAD
    )


def is_listed(
    *, timing: Timing, now: datetime, lookahead: timedelta | None,
) -> bool:
    """
    Say whether an alert is close enough to list.

    Parameters
    ----------
    timing : Timing
        Its judged window.
    now : datetime
        Current time.
    lookahead : timedelta | None
        How far ahead coming alerts are listed; None for no limit.

    Returns
    -------
    bool
        True when it is listed.
    """
    start = timing.window.start
    return (
        timing.active
        or lookahead is None
        or (start is not None and start - now <= lookahead)
    )


def rank_key(
    listed: Listed,
) -> tuple[bool, bool, int, bool, datetime, str, str]:
    """
    Sort key for the list.

    Parameters
    ----------
    listed : Listed
        A listed alert.

    Returns
    -------
    tuple[bool, bool, int, bool, datetime, str, str]
        Open-ended after dated, coming after active, then tie, start,
        header and ID.
    """
    alert = listed.matched.alert
    return (
        listed.timing.window.end is None,
        not listed.timing.active,
        listed.matched.tie,
        *window_key(listed.timing.window),
        alert.header,
        alert.alert_id,
    )


def listed_alerts(
    *,
    matched: Iterable[Matched],
    now: datetime,
    lookahead: timedelta | None,
) -> tuple[Listed, ...]:
    """
    The alerts to list, ranked.

    Parameters
    ----------
    matched : Iterable[Matched]
        Alerts tied to the pin.
    now : datetime
        Current time.
    lookahead : timedelta | None
        How far ahead coming alerts are listed; None for no limit.

    Returns
    -------
    tuple[Listed, ...]
        Dated before open-ended, active before coming, then trip, stop
        and route ties, then by start, header and ID.
    """
    found = []
    for item in matched:
        timing = timing_of(alert=item.alert, now=now)
        if timing is None:
            continue
        if not is_listed(timing=timing, now=now, lookahead=lookahead):
            continue
        banner = is_banner(matched=item, timing=timing, now=now)
        found.append(Listed(matched=item, timing=timing, banner=banner))
    return tuple(sorted(found, key=rank_key))


def alert_banner(
    *, listed: Sequence[Listed], now: datetime,
) -> AlertBanner | None:
    """
    The banner line for the first banner-eligible alert.

    Parameters
    ----------
    listed : Sequence[Listed]
        Ranked listed alerts.
    now : datetime
        Current time.

    Returns
    -------
    AlertBanner | None
        The line, or None when no alert may take the banner.
    """
    eligible = [item for item in listed if item.banner]
    if not eligible:
        return None
    first = eligible[0]
    text = first.matched.alert.header
    start = first.timing.window.start
    if not first.timing.active and start is not None:
        text = f'From {when_text(moment=start, now=now)}{DOT}{text}'
    return AlertBanner(
        text=text, active=first.timing.active, more=len(eligible) - 1,
    )


def when_text(*, moment: datetime, now: datetime) -> str:
    """
    A time, with as much of the date as it needs.

    Parameters
    ----------
    moment : datetime
        Time to show.
    now : datetime
        Current time.

    Returns
    -------
    str
        '23:40' today, 'Sat 23:40' within six days, else
        'Sat 7 Nov 23:40'.
    """
    local = moment.astimezone(SYDNEY)
    days = (local.date() - now.astimezone(SYDNEY).date()).days
    clock = clock_text(moment=moment)
    if days == 0:
        return clock
    if 0 < days <= WEEK_DAYS:
        return f'{local:%a} {clock}'
    return f'{local:%a} {local.day} {local:%b} {clock}'


def time_line(*, timing: Timing, now: datetime) -> str:
    """
    The list's time line for an alert.

    Parameters
    ----------
    timing : Timing
        Its judged window.
    now : datetime
        Current time.

    Returns
    -------
    str
        For example '21:50 to Tue 1:30 · +1 more'.
    """
    start, end = timing.window.start, timing.window.end
    until = None if end is None else when_text(moment=end, now=now)
    if timing.active or start is None:
        line = 'Ongoing' if until is None else f'Now until {until}'
    else:
        since = when_text(moment=start, now=now)
        line = (
            f'From {since}, ongoing' if until is None
            else f'{since} to {until}'
        )
    return f'{line}{DOT}+{timing.more} more' if timing.more else line
