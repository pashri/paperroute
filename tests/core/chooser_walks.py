"""Chooser columns, a strip and walks through the chooser for tests."""

from datetime import datetime, timedelta
from typing import Final

from paperroute.city import SydneyMode
from paperroute.core.chooser import (
    Choice,
    ChooserPath,
    ChooserRequest,
    ChooserState,
    Column,
    ColumnRequest,
    Level,
    opened,
    pressed,
    turned,
    with_column,
    with_line,
)
from paperroute.core.chooser_station import knob_pressed
from paperroute.core.messages import ColumnLoaded, Event, LineLoaded
from paperroute.core.state_model import RELOAD_BEFORE
from paperroute.core.stop_view import HORIZON
from paperroute_hardware.core.knob_events import Knob, KnobPressed, KnobTurned
from paperroute_testkit.core.factories import START, departure, place
from paperroute_transit.core.model import Departure, Pin, StopPlace
from paperroute_transit.core.reach import Reach, ReachRow, RowKind, StripKey

KINDS: Final[Column] = Column(
    level=Level.KIND,
    items=(
        Choice(key='bus', label='Bus', badge='B'),
        Choice(key='train', label='Train', badge='T'),
        Choice(key='metro', label='Metro', badge='M'),
    ),
)
GROUPS: Final[Column] = Column(
    level=Level.GROUP,
    items=(
        Choice(key='1-99', label='1-99'),
        Choice(key='600s', label='600s'),
    ),
)
ROUTES: Final[Column] = Column(
    level=Level.ROUTE,
    items=(
        Choice(key='r606', label='Parramatta to Hawkesbury', badge='606'),
        Choice(key='r609', label='Parramatta to Castle Hill', badge='609'),
    ),
)
TOWARDS: Final[Column] = Column(
    level=Level.TOWARDS,
    items=(
        Choice(key='11', label='Parramatta', detail='40'),
        Choice(key='12', label='Hawkesbury', detail='38'),
    ),
)
REPLACEMENT_GROUPS: Final[Column] = Column(
    level=Level.GROUP,
    items=(
        *GROUPS.items,
        Choice(
            key='replacements', label='Trackwork', rule_above=True,
            level=Level.REPLACEMENTS,
        ),
    ),
)
REPLACEMENT_LINES: Final[Column] = Column(
    level=Level.LINE,
    items=(Choice(key='T1', label='North Shore & Western', badge='T1'),),
)
REPLACEMENT_ROUTES: Final[Column] = Column(
    level=Level.ROUTE,
    items=(Choice(key='rep:1T1', label='Central to Penrith', badge='1T1'),),
)
BUS: Final[ChooserPath] = ((Level.KIND, 'bus'),)
REPLACEMENTS_PATH: Final[ChooserPath] = (
    *BUS, (Level.REPLACEMENTS, 'replacements'),
)
METRO_LINES: Final[Column] = Column(
    level=Level.LINE,
    items=(Choice(key='m1', label='North West & Bankstown', badge='M1'),),
)
TRAIN_LINES: Final[Column] = Column(
    level=Level.LINE,
    items=(
        Choice(key='t1', label='North Shore & Western', badge='T1'),
        Choice(key='t2', label='Leppington & Inner West', badge='T2'),
    ),
)
T1_PLACES: Final[dict[str, StopPlace]] = {
    stop_id: place(stop_id, name=name, point=(150.0 + index / 10, -33.6))
    for index, (stop_id, name) in enumerate((
        ('R1', 'Richmond'),
        ('R2', 'Riverstone'),
        ('E1', 'Emu Plains'),
        ('E2', 'Penrith'),
    ))
}


def station(stop_id: str, *, dim: bool = False, tag: str = '') -> Choice:
    """
    A station row of a T1 STATION or TO column.

    Parameters
    ----------
    stop_id : str
        Station ID: R1, R2, E1 or E2.
    dim : bool
        Whether it is greyed out.
    tag : str
        Its tag; 'ALL' on the From.

    Returns
    -------
    Choice
        The row, with its place.
    """
    found = T1_PLACES[stop_id]
    return Choice(
        key=stop_id, label=found.name, tag=tag, dim=dim, place=found,
    )


def heading(label: str) -> Choice:
    """
    A block heading of a T1 STATION or TO column.

    Parameters
    ----------
    label : str
        Its text.

    Returns
    -------
    Choice
        The heading row.
    """
    return Choice(key='', label=label, heading=True)


T1_STATIONS: Final[Column] = Column(
    level=Level.STATION,
    items=(
        heading('To Richmond'),
        station('R1'),
        station('R2'),
        heading('To Emu Plains'),
        station('E1'),
        station('E2'),
    ),
)
T1_TO: Final[Column] = Column(
    level=Level.TO,
    items=(
        heading('To Richmond'),
        station('R1', dim=True),
        station('R2'),
        heading('To Emu Plains'),
        station('E1', dim=True),
        station('E2', tag='ALL'),
    ),
)  # from Penrith
T1_TO_END: Final[Column] = Column(
    level=Level.TO,
    items=(
        heading('To Richmond'),
        station('R1', dim=True),
        station('R2', dim=True),
        heading('To Emu Plains'),
        station('E1', tag='ALL'),
        station('E2', dim=True),
    ),
)  # from Emu Plains, where nothing else is reached
T1: Final[ChooserPath] = ((Level.KIND, 'train'), (Level.LINE, 't1'))
LINE: Final[tuple[StopPlace, ...]] = (
    place('S1', name='Parramatta'),
    place('S2', name='Northmead'),
    place('S3', name='Baulkham Hills'),
)
BUS_606: Final[ChooserPath] = (
    (Level.KIND, 'bus'),
    (Level.GROUP, '600s'),
    (Level.ROUTE, 'r606'),
)


CHOSEN: Final[Pin] = Pin(
    origin=LINE[1], kind=SydneyMode.BUS, routes=('606',),
)
CHOSEN_KEY: Final[StripKey] = StripKey(
    station='S2', kind=SydneyMode.BUS, routes=('606',),
)
UNTIL: Final[datetime] = START + HORIZON + RELOAD_BEFORE
OPEN: Final[KnobPressed] = KnobPressed(knob=Knob.TOP)
NEXT: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)
DOWN: Final[KnobTurned] = KnobTurned(knob=Knob.BOTTOM, steps=1)
PENRITH: Final[Pin] = Pin(
    origin=T1_PLACES['E2'], kind=SydneyMode.TRAIN, routes=('T1',),
)
TO_COLUMN: Final[tuple[Event, ...]] = (
    OPEN,
    ColumnLoaded(path=(), column=KINDS),
    ColumnLoaded(path=BUS_606[:1], column=GROUPS),
    DOWN,
    ColumnLoaded(path=T1[:1], column=TRAIN_LINES),
    NEXT,
    ColumnLoaded(path=T1, column=T1_STATIONS),
    NEXT,
    KnobTurned(knob=Knob.BOTTOM, steps=3),
    NEXT,
    ColumnLoaded(path=(*T1, (Level.STATION, 'E2')), column=T1_TO),
)
TO_STAGE: Final[tuple[Event, ...]] = (
    OPEN,
    ColumnLoaded(path=(), column=KINDS),
    ColumnLoaded(path=BUS_606[:1], column=GROUPS),
    NEXT,
    DOWN,
    ColumnLoaded(path=BUS_606[:2], column=ROUTES),
    NEXT,
    ColumnLoaded(path=BUS_606, column=TOWARDS),
    NEXT,
    NEXT,
    LineLoaded(towards=11, line=LINE),
    DOWN,
    NEXT,
)


def row(stop_id: str, kind: RowKind) -> ReachRow:
    """
    A strip row with no indent.

    Parameters
    ----------
    stop_id : str
        Station ID.
    kind : RowKind
        What the row shows.

    Returns
    -------
    ReachRow
        The row.
    """
    return ReachRow(
        place=place(stop_id), kind=kind, depth=0, above=False, parent=None,
    )


REACH: Final[Reach] = Reach(
    rows=(
        row('A1', RowKind.APPROACH),
        row('S2', RowKind.ORIGIN),
        row('S3', RowKind.MAIN),
        row('S4', RowKind.MAIN),
    ),
    stations=2,
    station_of={},
)


def fed(
    chooser: ChooserState,
    request: ChooserRequest | Pin | None,
    column: Column | None,
) -> tuple[ChooserState, ChooserRequest | Pin | None]:
    """
    Answer a column request.

    Parameters
    ----------
    chooser : ChooserState
        Chooser waiting for a column.
    request : ChooserRequest | Pin | None
        Its request; must be a ColumnRequest.
    column : Column | None
        The answer.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | Pin | None]
        The chooser and its next request.
    """
    assert isinstance(request, ColumnRequest)
    return with_column(chooser=chooser, path=request.path, column=column)


def opened_at_kind(kind: SydneyMode | None = None) -> ChooserState:
    """
    Open the chooser and load the KIND column.

    Parameters
    ----------
    kind : SydneyMode | None
        Shown place's kind.

    Returns
    -------
    ChooserState
        The chooser, waiting for the KIND cursor's preview.
    """
    chooser, request = opened(shown=Pin(origin=place('X'), kind=kind))
    chooser, _ = fed(chooser, request, KINDS)
    return chooser


def at_towards() -> ChooserState:
    """
    Walk Bus, 600s, 606 to the focused TOWARDS column.

    Returns
    -------
    ChooserState
        The chooser focused on 606's TOWARDS column.
    """
    chooser = opened_at_kind()
    chooser, request = fed(chooser, ColumnRequest(path=BUS_606[:1]), GROUPS)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = fed(chooser, request, None)  # 1-99 fails
    chooser, request = turned(chooser=chooser, steps=1, reach=None)
    chooser, request = fed(chooser, request, ROUTES)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = fed(chooser, request, TOWARDS)
    chooser, request = pressed(chooser=chooser, reach=None)
    assert request is None
    return chooser


def at_replacement_towards() -> ChooserState:
    """
    Walk Bus, Replacement buses, T1, 1T1 to the focused TOWARDS column.

    Returns
    -------
    ChooserState
        The chooser focused on the replacement route's TOWARDS column.
    """
    chooser = opened_at_kind()
    chooser, _ = fed(chooser, ColumnRequest(path=BUS), REPLACEMENT_GROUPS)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = fed(chooser, request, None)  # 1-99 fails
    chooser, request = turned(chooser=chooser, steps=2, reach=None)
    assert request == ColumnRequest(path=REPLACEMENTS_PATH)
    chooser, request = fed(chooser, request, REPLACEMENT_LINES)
    chooser, request = fed(chooser, request, REPLACEMENT_ROUTES)
    chooser, request = fed(chooser, request, TOWARDS)
    for _ in range(3):
        chooser, request = pressed(chooser=chooser, reach=None)
    assert request is None
    assert chooser.focus == 4
    return chooser


def at_from() -> ChooserState:
    """
    Press 606's first towards row and load its line.

    Returns
    -------
    ChooserState
        The chooser in the FROM stage, cursor on the first station.
    """
    chooser, _ = pressed(chooser=at_towards(), reach=None)
    return with_line(chooser=chooser, towards=11, line=LINE)


def at_to() -> ChooserState:
    """
    Pick Northmead as the From.

    Returns
    -------
    ChooserState
        The chooser in the TO stage, cursor on the From.
    """
    chooser, _ = turned(chooser=at_from(), steps=1, reach=None)
    chooser, _ = pressed(chooser=chooser, reach=None)
    return chooser


def at_stations() -> ChooserState:
    """
    Walk Train and T1 to the focused STATION column.

    Returns
    -------
    ChooserState
        The chooser focused on T1's stations, cursor on Richmond.
    """
    chooser = opened_at_kind(kind=SydneyMode.TRAIN)
    chooser, request = fed(chooser, ColumnRequest(path=T1[:1]), TRAIN_LINES)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = fed(chooser, request, T1_STATIONS)
    chooser, request = pressed(chooser=chooser, reach=None)
    assert request is None
    return chooser


def at_to_column(
    *, steps: int = 3, column: Column = T1_TO,
) -> ChooserState:
    """
    Press a T1 station and load its TO column.

    Parameters
    ----------
    steps : int
        Detents from Richmond to the From; 3 reaches Penrith.
    column : Column
        The TO column answered.

    Returns
    -------
    ChooserState
        The chooser focused on the TO column.
    """
    chooser, _ = turned(chooser=at_stations(), steps=steps, reach=None)
    chooser, request = knob_pressed(chooser=chooser, reach=None)
    chooser, _ = fed(chooser, request, column)
    return chooser


def chooser_departures() -> tuple[Departure, ...]:
    """
    Make 606 departures from the chooser's From, one already on its way.

    Returns
    -------
    tuple[Departure, ...]
        A 606 that left a minute ago and one in five minutes, both to
        Hawkesbury.
    """
    return tuple(
        departure(
            departs=START + timedelta(minutes=minutes),
            trip_id=f'c-{index}',
            headsign='Hawkesbury',
            kind=SydneyMode.BUS,
        )
        for index, minutes in enumerate((-1, 5))
    )
