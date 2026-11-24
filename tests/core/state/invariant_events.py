"""Shared helpers for the . tests: invariant events."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final

from hypothesis import strategies as st

from paperroute_device.core.kit_events import QuitRequested, Tick
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobTurned,
)
from paperroute_sydney.city import (
    SYDNEY,
    SYDNEY_ZONE,
    TOWN_HALL,
    SydneyFeed,
    SydneyMode,
)
from paperroute_testkit.core.factories import (
    departure,
    place,
    snapshot,
    vehicle,
)
from paperroute_transit.core.chooser import (
    ChooserPath,
    ChooserState,
    Column,
    Level,
    Stage,
    chosen_pin,
    path_to,
)
from paperroute_transit.core.chooser_lists import preview_steps
from paperroute_transit.core.chooser_previews import read_previews
from paperroute_transit.core.geo import make_shape
from paperroute_transit.core.map_state import area_box
from paperroute_transit.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    DeparturesFailed,
    DeparturesLoaded,
    Event,
    FeedFailed,
    FeedFetched,
    LineFailed,
    LineLoaded,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapDataFailed,
    MapRequest,
    ModesLoaded,
    PatternsLoaded,
    PlacesMatched,
    PositionsFetched,
    PreviewsFailed,
    PreviewsLoaded,
    ShapesLoaded,
    StripFailed,
    StripLoaded,
    TimetableSeen,
)
from paperroute_transit.core.model import Pin, shifted
from paperroute_transit.core.modes import end_point_modes
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    RunTail,
    build_reach,
)
from paperroute_transit.core.realtime import RealtimeSnapshot
from paperroute_transit.core.state_model import State, load_window
from paperroute_transit.core.strip_state import strip_key
from paperroute_transit.core.tiles import ZOOMS
from tests.core.chooser_walks import (
    BUS_606,
    CHOSEN,
    DOWN,
    GROUPS,
    KINDS,
    LINE,
    METRO_LINES,
    NEXT,
    OPEN,
    PENRITH,
    ROUTES,
    T1_STATIONS,
    T1_TO,
    TOWARDS,
    TRAIN_LINES,
    UNTIL,
    chooser_departures,
)
from tests.core.state.helpers import (
    BUS_POSITIONS,
    BUSES,
    CENTRE,
    ELSEWHERE,
    FERRY_POSITIONS,
    PIN,
    PINS,
)

ENDS: Final[frozenset[str]] = end_point_modes(modes=SYDNEY.modes)


POOL: Final[tuple[Pin, ...]] = (
    PIN,
    *PINS,
    ELSEWHERE,
    Pin(origin=TOWN_HALL, kind=SydneyMode.TRAIN),
)


FALL_BACK_EVE = datetime(2026, 4, 5, 1, 30, tzinfo=SYDNEY_ZONE)


MODES = [
    SydneyFeed.BUS,
    SydneyFeed.TRAIN,
    SydneyFeed.REGIONAL,
    SydneyFeed.FERRY,
]


@st.composite
def map_events(draw: st.DrawFn, now: datetime) -> Event:
    """
    A random map data event.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.
    now : datetime
        Current time.

    Returns
    -------
    Event
        Shapes, patterns, modes, positions or a failure.
    """
    trips = frozenset(draw(st.sets(st.sampled_from(['trip-0800', 'x']))))
    mpp = draw(st.sampled_from(ZOOMS))
    line = make_shape(shape_id='b:1', points=[CENTRE, (0.0, 0.0)])
    bus = vehicle(timestamp=int(now.timestamp()), point=CENTRE)
    requests: list[MapRequest] = [
        LoadShapes(shape_ids=frozenset({'b:1', 'b:2'})),
        LoadPatterns(trip_ids=trips, day=now.date()),
        LoadModes(mpp=mpp, box=area_box(centre=CENTRE, mpp=mpp)),
    ]
    request = draw(st.sampled_from(requests))
    feed = draw(st.sampled_from([BUS_POSITIONS, FERRY_POSITIONS]))
    options: list[Event] = [
        ShapesLoaded(shapes={'b:1': line}),
        PatternsLoaded(trip_ids=trips, patterns={}, shapes={}),
        ModesLoaded(
            mpp=mpp,
            box=area_box(centre=CENTRE, mpp=mpp),
            modes=frozenset(draw(st.sets(st.sampled_from(MODES)))),
        ),
        PositionsFetched(
            feed=feed,
            snapshot=snapshot(fetched_at=now, vehicles=(bus,)),
        ),
        MapDataFailed(request=request),
    ]
    return draw(st.sampled_from(options))


VERSIONS: Final[tuple[str | None, ...]] = (None, 'v1', 'v2')


STOP_IDS: Final[tuple[str, ...]] = (
    '2145615',
    '215020',
    'first',
    'second',
    'third',
    'elsewhere',
    TOWN_HALL.stop_id,
)


@st.composite
def found_places(draw: st.DrawFn) -> PlacesMatched:
    """
    A random answer to a re-match.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    PlacesMatched
        Some stop IDs each found under itself, another ID, or not at all.
    """
    found = draw(
        st.dictionaries(
            keys=st.sampled_from(STOP_IDS),
            values=st.one_of(
                st.none(),
                st.sampled_from(STOP_IDS).map(
                    lambda stop_id: place(
                        stop_id, name=stop_id, point=(1.0, 2.0)
                    )
                ),
            ),
        )
    )
    return PlacesMatched(found=found)


@st.composite
def place_events(draw: st.DrawFn) -> Event:
    """
    A random hold, timetable version or re-match.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Event
        A knob hold, a timetable seen or places matched.
    """
    options: list[Event] = [
        KnobHeld(knob=draw(st.sampled_from(list(Knob)))),
        TimetableSeen(version=draw(st.sampled_from(VERSIONS))),
        draw(found_places()),
    ]
    return draw(st.sampled_from(options))


def reach_through(*, origin: str, side: int) -> Reach:
    """
    Build a reach with the same number of stations each side of a From.

    Parameters
    ----------
    origin : str
        From stop ID.
    side : int
        Stations each side.

    Returns
    -------
    Reach
        The reach.
    """
    ups = tuple(f'up-{i}' for i in range(side))
    downs = tuple(f'down-{i}' for i in range(side))
    return build_reach(
        data=ReachInput(
            origin=place(origin, name=origin),
            tails=(
                RunTail(before=(), after=ups, trips=2),
                RunTail(before=(), after=downs, trips=1),
            ),
            places={key: place(key, name=key) for key in (*ups, *downs)},
            station_of={},
        )
    )


@st.composite
def strip_events(draw: st.DrawFn) -> Event:
    """
    A random reach built or failed, for any place's strip or another.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Event
        A StripLoaded with no reach, a short or a long one, or a
        StripFailed, from any timetable version.
    """
    pin = draw(st.sampled_from(POOL))
    key = draw(
        st.sampled_from(
            [
                strip_key(pin=pin, end_points=ENDS),
                replace(strip_key(pin=pin, end_points=ENDS), station='x'),
            ]
        )
    )
    side = draw(st.sampled_from([0, 2, 20]))
    reach = draw(
        st.sampled_from(
            [None, reach_through(origin=pin.origin.stop_id, side=side)]
        )
    )
    timetable = draw(st.sampled_from(VERSIONS))
    options: list[Event] = [
        StripLoaded(key=key, reach=reach, timetable=timetable),
        StripFailed(key=key, timetable=timetable),
    ]
    return draw(st.sampled_from(options))


class Outcome(StrEnum):
    """How a stand-in reply turns out."""

    EXPECTED = 'expected'
    EMPTY = 'empty'
    FAILED = 'failed'


@dataclass(frozen=True)
class Answer:
    """
    A stand-in for the reply to whatever the chooser waits for.

    Attributes
    ----------
    outcome : Outcome
        The expected answer, an empty one or a failure.
    current : bool
        Whether it comes from the timetable in use.
    side : int
        Stations each side of the From on a strip answered.
    """

    outcome: Outcome
    current: bool
    side: int


def column_for(*, path: ChooserPath) -> Column:
    """
    Answer a column request from a small catalogue.

    Parameters
    ----------
    path : ChooserPath
        The path asked for.

    Returns
    -------
    Column
        Bus or Metro routes and their towards rows, or T1's stations.
    """
    known = {
        (): KINDS,
        BUS_606[:1]: GROUPS,
        ((Level.KIND, 'train'),): TRAIN_LINES,
        ((Level.KIND, 'metro'),): METRO_LINES,
    }
    if path in known:
        return known[path]
    match path[-1][0]:
        case Level.GROUP:
            return ROUTES
        case Level.LINE if path[0][1] == 'train':
            return T1_STATIONS
        case Level.STATION:
            return T1_TO
    return TOWARDS


@dataclass(frozen=True)
class SmallCatalogue:
    """A network that answers every path from the small catalogue."""

    def choices(self, *, path: ChooserPath) -> Column | None:
        """
        Give the column after a path.

        Parameters
        ----------
        path : ChooserPath
            Steps chosen so far.

        Returns
        -------
        Column | None
            The small catalogue's column.
        """
        return column_for(path=path)


def previews_reply(
    *, chooser: ChooserState, answer: Answer, timetable: str | None,
) -> Event:
    """
    Answer the previews load a focus asked for.

    Parameters
    ----------
    chooser : ChooserState
        Chooser whose focus has its previews being read.
    answer : Answer
        The stand-in.
    timetable : str | None
        The version the reply comes from.

    Returns
    -------
    Event
        The previews, empty for every row when unexpected, or the failure.
    """
    path = path_to(chooser=chooser, index=chooser.focus - 1)
    if answer.outcome is Outcome.FAILED:
        return PreviewsFailed(path=path, timetable=timetable)
    column = chooser.columns[chooser.focus]
    steps = preview_steps(column=column)
    if answer.outcome is Outcome.EXPECTED:
        previews = read_previews(
            network=SmallCatalogue(), path=path, steps=steps,
        )
    else:
        previews = tuple(() for _ in steps)
    return PreviewsLoaded(path=path, previews=previews, timetable=timetable)


def picked_reply(
    *, state: State, answer: Answer, timetable: str | None
) -> Event:
    """
    Answer the TO stage's strip or departures load.

    Parameters
    ----------
    state : State
        State with the chooser in the TO stage.
    answer : Answer
        The stand-in.
    timetable : str | None
        The version the reply comes from.

    Returns
    -------
    Event
        The strip, built or not, while it is pending; else departures.
    """
    chooser = state.chooser
    assert chooser is not None and chooser.key is not None
    pin = chosen_pin(chooser=chooser, destination=None)
    pending = chooser.key in state.strip.pending
    if pending and answer.outcome is not Outcome.EMPTY:
        reach = (
            reach_through(origin=pin.origin.stop_id, side=answer.side)
            if answer.outcome is Outcome.EXPECTED
            else None
        )
        return StripLoaded(key=chooser.key, reach=reach, timetable=timetable)
    if answer.outcome is Outcome.FAILED:
        return ChooserDeparturesFailed(pin=pin, timetable=timetable)
    return ChooserDeparturesLoaded(
        pin=pin,
        departures=chooser_departures(),
        until=load_window(now=state.now, zone=SYDNEY_ZONE)[1],
        timetable=timetable,
    )


def listed_reply(
    *,
    chooser: ChooserState,
    answer: Answer,
    now: datetime,
    timetable: str | None,
) -> Event:
    """
    Answer the departures load a station press asked for.

    Parameters
    ----------
    chooser : ChooserState
        Chooser with a From chosen in its lists.
    answer : Answer
        The stand-in.
    now : datetime
        Time of the reply.
    timetable : str | None
        The version the reply comes from.

    Returns
    -------
    Event
        The departures, or their failure.
    """
    pin = chosen_pin(chooser=chooser, destination=None)
    if answer.outcome is Outcome.FAILED:
        return ChooserDeparturesFailed(pin=pin, timetable=timetable)
    return ChooserDeparturesLoaded(
        pin=pin,
        departures=chooser_departures(),
        until=load_window(now=now, zone=SYDNEY_ZONE)[1],
        timetable=timetable,
    )


def answered(*, state: State, answer: Answer) -> Event:
    """
    Turn a stand-in into the reply the chooser waits for.

    Parameters
    ----------
    state : State
        State.
    answer : Answer
        The stand-in.

    Returns
    -------
    Event
        A column, line, strip or departures reply; a Tick when nothing is
        waited for.
    """
    chooser = state.chooser
    timetable = state.saving.timetable if answer.current else 'old'
    if chooser is None:
        return Tick(now=state.now)
    if chooser.stage is Stage.TO:
        return picked_reply(state=state, answer=answer, timetable=timetable)
    if chooser.origin is not None and chooser.waiting is None:
        return listed_reply(
            chooser=chooser, answer=answer, now=state.now,
            timetable=timetable,
        )
    if chooser.stage is Stage.FROM and chooser.towards is not None:
        return line_reply(
            towards=int(chooser.towards.key),
            answer=answer,
            timetable=timetable,
        )
    return waiting_reply(
        state=state, chooser=chooser, answer=answer, timetable=timetable,
    )


def line_reply(
    *, towards: int, answer: Answer, timetable: str | None
) -> Event:
    """
    Answer the line a Towards waits for.

    Parameters
    ----------
    towards : int
        The Towards the line is for.
    answer : Answer
        The stand-in.
    timetable : str | None
        The timetable version the reply names.

    Returns
    -------
    Event
        The line, or its failure.
    """
    if answer.outcome is Outcome.FAILED:
        return LineFailed(towards=towards, timetable=timetable)
    line = LINE if answer.outcome is Outcome.EXPECTED else None
    return LineLoaded(towards=towards, line=line, timetable=timetable)


def waiting_reply(
    *,
    state: State,
    chooser: ChooserState,
    answer: Answer,
    timetable: str | None,
) -> Event:
    """
    Answer the column or previews the chooser waits for.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        The chooser.
    answer : Answer
        The stand-in.
    timetable : str | None
        The timetable version the reply names.

    Returns
    -------
    Event
        A column or previews reply; a Tick when nothing is waited for.
    """
    path = chooser.waiting
    if (
        path is None
        and chooser.stage is Stage.LISTS
        and chooser.columns
        and chooser.columns[chooser.focus].previews == ()
    ):
        return previews_reply(
            chooser=chooser, answer=answer, timetable=timetable,
        )
    if path is None:
        return Tick(now=state.now)
    if answer.outcome is Outcome.FAILED:
        return ColumnFailed(path=path, timetable=timetable)
    column = (
        column_for(path=path)
        if answer.outcome is Outcome.EXPECTED
        else Column(level=Level.ROUTE, items=())
    )
    return ColumnLoaded(path=path, column=column, timetable=timetable)


@st.composite
def chooser_events(draw: st.DrawFn) -> Event:
    """
    A random chooser reply, most likely for something not waited for.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Event
        A column, line or departures reply or failure, from any
        timetable version.
    """
    timetable = draw(st.sampled_from(VERSIONS))
    path = draw(st.sampled_from([(), BUS_606[:1], BUS_606[:2], BUS_606]))
    towards = draw(st.sampled_from([11, 12, 21]))
    pin = draw(st.sampled_from([CHOSEN, PENRITH, *POOL]))
    options: list[Event] = [
        ColumnLoaded(
            path=path, column=column_for(path=path), timetable=timetable,
        ),
        ColumnFailed(path=path, timetable=timetable),
        PreviewsLoaded(
            path=path,
            previews=read_previews(
                network=SmallCatalogue(),
                path=path,
                steps=preview_steps(column=column_for(path=path)),
            ),
            timetable=timetable,
        ),
        PreviewsFailed(path=path, timetable=timetable),
        LineLoaded(towards=towards, line=LINE, timetable=timetable),
        LineFailed(towards=towards, timetable=timetable),
        ChooserDeparturesLoaded(
            pin=pin,
            departures=chooser_departures(),
            until=UNTIL,
            timetable=timetable,
        ),
        ChooserDeparturesFailed(pin=pin, timetable=timetable),
    ]
    return draw(st.sampled_from(options))


@st.composite
def train_walk(draw: st.DrawFn) -> list[Event | Answer]:
    """
    The start of a walk to a T1 station and its TO column and back.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    list[Event | Answer]
        Knob events, and stand-ins for the replies, cut short at random.
    """
    ok = Answer(outcome=Outcome.EXPECTED, current=True, side=0)
    up = KnobTurned(knob=Knob.BOTTOM, steps=-9)
    walk: list[Event | Answer] = [
        OPEN,
        *(ok, ok, up, ok, DOWN, ok, NEXT, ok, NEXT),
        KnobTurned(knob=Knob.BOTTOM, steps=draw(st.integers(0, 3))),
        *(NEXT, ok, ok, DOWN, OPEN, OPEN, NEXT, ok),
    ]
    return walk[:draw(st.integers(1, len(walk)))]


@st.composite
def event_lists(
    draw: st.DrawFn, start: datetime
) -> list[Event | Answer]:
    """
    Random but time-ordered event sequences.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.
    start : datetime
        Time of the first event.

    Returns
    -------
    list[Event | Answer]
        Events, and stand-ins for the replies the chooser waits for.
    """
    now, events = start, list[Event | Answer]()
    kinds = [
        'tick',
        'turn',
        'press',
        'fail',
        'ok',
        'load',
        'unreadable',
        'map',
        'place',
        'strip',
        'quit',
        'walk',
        'train',
        'answer',
        'answer',
        'chooser',
        'held',
    ]
    for kind in draw(st.lists(st.sampled_from(kinds), max_size=40)):
        if kind == 'tick':
            now = shifted(
                moment=now,
                seconds=draw(st.integers(0, 900)),
                zone=SYDNEY_ZONE,
            )
        events.extend(draw(events_of_kind(kind=kind, now=now)))
    return events


@st.composite
def events_of_kind(
    draw: st.DrawFn, kind: str, now: datetime
) -> list[Event | Answer]:
    """
    Random events of one kind, for the knobs, feeds and map.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.
    kind : str
        The kind of event; the chooser's kinds go to `chooser_kind_events`.
    now : datetime
        The time of the event.

    Returns
    -------
    list[Event | Answer]
        The events drawn.
    """
    knob = draw(st.sampled_from(list(Knob)))
    found: list[Event | Answer]
    if kind == 'tick':
        found = [Tick(now=now)]
    elif kind == 'turn':
        found = [KnobTurned(knob=knob, steps=draw(st.integers(-10, 10)))]
    elif kind == 'press':
        found = [KnobPressed(knob=knob)]
    elif kind == 'held':
        found = [KnobHeldTurned(knob=knob, steps=draw(st.integers(-6, 6)))]
    elif kind == 'fail':
        found = [FeedFailed(feed=BUSES, at=now, reason='timeout')]
    elif kind == 'ok':
        snap = RealtimeSnapshot(fetched_at=now, trips={})
        found = [FeedFetched(feed=BUSES, snapshot=snap)]
    elif kind == 'quit':
        found = [QuitRequested()]
    elif kind == 'map':
        found = [draw(map_events(now=now))]
    else:
        found = draw(chooser_kind_events(kind=kind, now=now))
    return found


@st.composite
def chooser_kind_events(
    draw: st.DrawFn, kind: str, now: datetime
) -> list[Event | Answer]:
    """
    Random events of one kind, for the places, strip and chooser.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.
    kind : str
        The kind of event; anything not named is a departures load.
    now : datetime
        The time of the event.

    Returns
    -------
    list[Event | Answer]
        The events drawn.
    """
    found: list[Event | Answer]
    if kind == 'unreadable':
        found = [
            DeparturesFailed(
                pin=draw(st.sampled_from(POOL)),
                timetable=draw(st.sampled_from(VERSIONS)),
            )
        ]
    elif kind == 'place':
        found = [draw(place_events())]
    elif kind == 'strip':
        found = [draw(strip_events())]
    elif kind == 'walk':
        ok = Answer(
            outcome=Outcome.EXPECTED,
            current=True,
            side=draw(st.sampled_from([0, 2, 20])),
        )
        walk: list[Event | Answer] = [
            OPEN,
            *(ok, ok, NEXT),
            *(ok, NEXT),
            *(ok, NEXT),
            *(ok, NEXT),
            *(ok, DOWN, NEXT),
            *(ok, ok, DOWN, DOWN),
        ]
        found = walk[:draw(st.integers(1, len(walk)))]
    elif kind == 'train':
        found = draw(train_walk())
    elif kind == 'answer':
        found = [
            Answer(
                outcome=draw(st.sampled_from(Outcome)),
                current=draw(st.booleans()),
                side=draw(st.sampled_from([0, 2, 20])),
            )
        ]
    elif kind == 'chooser':
        found = [draw(chooser_events())]
    else:
        found = [draw(departures_loaded(now=now))]
    return found


@st.composite
def departures_loaded(draw: st.DrawFn, now: datetime) -> Event:
    """
    A random departures load for a pin.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.
    now : datetime
        The time the load is read.

    Returns
    -------
    Event
        The load.
    """
    count = draw(st.integers(0, 25))
    deps = tuple(
        replace(
            departure(
                departs=shifted(
                    moment=now,
                    seconds=120 * i,
                    zone=SYDNEY_ZONE,
                ),
                mode=draw(st.sampled_from(MODES)),
            ),
            shape_id=draw(st.sampled_from([None, 'b:1', 'b:2'])),
        )
        for i in range(count)
    )
    until = shifted(
        moment=now,
        seconds=3 * 3600 + 1800,
        zone=SYDNEY_ZONE,
    )
    return DeparturesLoaded(
        pin=draw(st.sampled_from(POOL)),
        departures=deps,
        until=until,
        timetable=draw(st.sampled_from(VERSIONS)),
    )
