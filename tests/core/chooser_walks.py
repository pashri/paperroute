"""Chooser columns, a strip and walks through the chooser for tests."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Final

from paperroute_hardware.core.knob_events import Knob, KnobPressed, KnobTurned
from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_testkit.core.factories import START, departure, place
from paperroute_transit.core.chooser import (
    Choice,
    ChooserPath,
    ChooserRequest,
    ChooserState,
    Column,
    ColumnRequest,
    Level,
    PreviewsRequest,
    opened,
)
from paperroute_transit.core.chooser_lists import (
    pressed,
    preview_steps,
    previewed,
    turned,
    with_column,
    with_previews,
)
from paperroute_transit.core.chooser_previews import (
    ColumnReader,
    read_previews,
)
from paperroute_transit.core.chooser_sides import with_line
from paperroute_transit.core.chooser_station import knob_pressed
from paperroute_transit.core.messages import (
    ColumnLoaded,
    Event,
    LineLoaded,
    PreviewsLoaded,
)
from paperroute_transit.core.model import Departure, Pin, StopPlace
from paperroute_transit.core.reach import Reach, ReachRow, RowKind, StripKey
from paperroute_transit.core.sides import (
    KerbPair,
    Kerbs,
    ParentKerb,
    ParentKerbs,
)
from paperroute_transit.core.state_model import RELOAD_BEFORE
from paperroute_transit.core.stop_view import HORIZON

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
THREE_ROUTES: Final[Column] = Column(
    level=Level.ROUTE,
    items=(
        *ROUTES.items,
        Choice(key='r610', label='Parramatta to Rouse Hill', badge='610'),
    ),
)
LONG_TOWARDS: Final[Column] = Column(
    level=Level.TOWARDS,
    items=tuple(
        Choice(key=str(30 + n), label=f'Stop {n}', detail='4')
        for n in range(14)
    ),
)
REPLACEMENT_GROUPS: Final[Column] = Column(
    level=Level.GROUP,
    items=(
        *GROUPS.items,
        Choice(
            key='replacement', label='Trackwork', rule_above=True,
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
    *BUS, (Level.REPLACEMENTS, 'replacement'),
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
TRAIN: Final[ChooserPath] = ((Level.KIND, 'train'),)
TRAIN_STATIONS: Final[Column] = Column(
    level=Level.STATION,
    items=tuple(
        Choice(key=key, label=found.name, place=found)
        for key, found in sorted(
            T1_PLACES.items(), key=lambda item: item[1].name,
        )
    ),
)  # Emu Plains, Penrith, Richmond, Riverstone
TRAIN_TO: Final[Column] = Column(
    level=Level.TO,
    items=(
        station('E2', tag='ALL'),
        station('R1'),
        station('R2'),
    ),
)  # from Penrith
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


@dataclass(frozen=True)
class Catalogue:
    """
    A network that answers the walks' paths.

    Attributes
    ----------
    columns : Mapping[ChooserPath, Column | None]
        Answers that replace the walks' own, by path.
    """

    columns: Mapping[ChooserPath, Column | None] = field(
        default_factory=dict,
    )

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
            The listed answer, else the walks' column.
        """
        if path in self.columns:
            return self.columns[path]
        return walk_column(path=path)


WALK: Final[Catalogue] = Catalogue()


def previews_of(
    *, path: ChooserPath, column: Column, catalogue: ColumnReader = WALK,
) -> PreviewsLoaded:
    """
    Make the reply to a column's previews request.

    Parameters
    ----------
    path : ChooserPath
        Steps leading to the column.
    column : Column
        The column whose rows are read.
    catalogue : ColumnReader
        The network that answers.

    Returns
    -------
    PreviewsLoaded
        What each row of the column opens.
    """
    return PreviewsLoaded(
        path=path,
        previews=read_previews(
            network=catalogue,
            path=path,
            steps=preview_steps(column=column),
        ),
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


def answered(
    chooser: ChooserState,
    request: ChooserRequest | Pin | None,
    catalogue: ColumnReader = WALK,
) -> tuple[ChooserState, ChooserRequest | Pin | None]:
    """
    Answer a column or previews request from a catalogue.

    Parameters
    ----------
    chooser : ChooserState
        Chooser waiting for the answer.
    request : ChooserRequest | Pin | None
        Its request.
    catalogue : ColumnReader
        The network that answers.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | Pin | None]
        The chooser and its next request; a previews answer asks for
        nothing, and any other request is returned unchanged.
    """
    if isinstance(request, ColumnRequest):
        return with_column(
            chooser=chooser,
            path=request.path,
            column=catalogue.choices(path=request.path),
        )
    if isinstance(request, PreviewsRequest):
        previews = read_previews(
            network=catalogue, path=request.path, steps=request.steps,
        )
        return with_previews(
            chooser=chooser, path=request.path, previews=previews,
        ), None
    return chooser, request


def opened_at_kind(
    kind: SydneyMode | None = None, catalogue: ColumnReader = WALK,
) -> ChooserState:
    """
    Open the chooser and load the KIND column and its previews.

    Parameters
    ----------
    kind : SydneyMode | None
        Shown place's kind.
    catalogue : ColumnReader
        The network that answers.

    Returns
    -------
    ChooserState
        The chooser on the KIND column with the cursor's preview shown.
    """
    chooser, asked = opened(
        shown=Pin(origin=place('X'), kind=kind),
        modes=SYDNEY.modes,
    )
    chooser, request = answered(chooser, asked, catalogue)
    chooser, request = answered(chooser, request, catalogue)
    assert request is None
    return chooser


def at_towards(*, catalogue: ColumnReader = WALK) -> ChooserState:
    """
    Walk Bus, 600s, 606 to the focused TOWARDS column.

    Parameters
    ----------
    catalogue : ColumnReader
        The network that answers.

    Returns
    -------
    ChooserState
        The chooser focused on 606's TOWARDS column.
    """
    chooser = opened_at_kind(catalogue=catalogue)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = answered(chooser, request, catalogue)
    chooser, request = turned(chooser=chooser, steps=1, reach=None)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = answered(chooser, request, catalogue)
    chooser, request = pressed(chooser=chooser, reach=None)
    assert request is None
    return chooser


ROUTE_STAGE: Final[Catalogue] = Catalogue(
    columns={
        (*BUS_606[:2], (Level.ROUTE, 'r609')): LONG_TOWARDS,
        (*BUS_606[:2], (Level.ROUTE, 'r610')): None,
    },
)


def at_routes(*, loaded: bool = True) -> ChooserState:
    """
    Focus the three-row ROUTE column on its first row.

    Parameters
    ----------
    loaded : bool
        Whether the column's previews have arrived.

    Returns
    -------
    ChooserState
        Chooser on KIND, GROUP and ROUTE with the cursor on 606.
    """
    chooser, request = previewed(
        chooser=ChooserState(
            columns=(KINDS, GROUPS, THREE_ROUTES),
            cursors=(0, 1, 0),
            focus=2,
        ),
    )
    if loaded:
        chooser, asked = answered(chooser, request, ROUTE_STAGE)
        assert asked is None
    return chooser


def at_replacement_towards() -> ChooserState:
    """
    Walk Bus, Replacement buses, T1, 1T1 to the focused TOWARDS column.

    Returns
    -------
    ChooserState
        The chooser focused on the replacement route's TOWARDS column.
    """
    catalogue = Catalogue(columns={BUS: REPLACEMENT_GROUPS})
    chooser = opened_at_kind(catalogue=catalogue)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = answered(chooser, request, catalogue)
    chooser, request = turned(chooser=chooser, steps=2, reach=None)
    assert request is None
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


def at_stations(*, catalogue: ColumnReader = WALK) -> ChooserState:
    """
    Walk Train and T1 to the focused STATION column.

    Parameters
    ----------
    catalogue : ColumnReader
        The network that answers.

    Returns
    -------
    ChooserState
        The chooser focused on T1's stations, cursor on Richmond.
    """
    chooser = opened_at_kind(kind=SydneyMode.TRAIN, catalogue=catalogue)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = answered(chooser, request, catalogue)
    chooser, request = pressed(chooser=chooser, reach=None)
    assert request is None
    return chooser


def at_to_column(
    *, steps: int = 3, catalogue: ColumnReader = WALK,
) -> ChooserState:
    """
    Press a T1 station and load its TO column.

    Parameters
    ----------
    steps : int
        Detents from Richmond to the From; 3 reaches Penrith.
    catalogue : ColumnReader
        The network that answers.

    Returns
    -------
    ChooserState
        The chooser focused on the TO column.
    """
    chooser, _ = turned(
        chooser=at_stations(catalogue=catalogue), steps=steps, reach=None,
    )
    chooser, request = knob_pressed(chooser=chooser, reach=None)
    chooser, _ = answered(chooser, request, catalogue)
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


def walk_column(*, path: ChooserPath) -> Column | None:
    """
    Answer the column after a path the walks take.

    Parameters
    ----------
    path : ChooserPath
        Steps chosen so far.

    Returns
    -------
    Column | None
        The walks' column, or None where they have none.
    """
    levels = tuple(level for level, _ in path)
    metro = ((Level.KIND, 'metro'),)
    replacement_lines = (*REPLACEMENTS_PATH, (Level.LINE, 'T1'))
    fixed: dict[ChooserPath, Column | None] = {
        (): KINDS,
        BUS: GROUPS,
        (*BUS, (Level.GROUP, '1-99')): None,
        BUS_606[:2]: ROUTES,
        REPLACEMENTS_PATH: REPLACEMENT_LINES,
        replacement_lines: REPLACEMENT_ROUTES,
        TRAIN: TRAIN_LINES,
        metro: METRO_LINES,
    }
    if path in fixed:
        return fixed[path]
    if path[-1:] == ((Level.ROUTE, 'rep:1T1'),) and (
        path[:-1] == replacement_lines
    ):
        return TOWARDS
    if path[:2] == BUS_606[:2] and levels[2:] == (Level.ROUTE,):
        return TOWARDS
    if path[:1] == TRAIN and levels[1:] == (Level.LINE,):
        return T1_STATIONS
    if path[:1] == TRAIN and levels[1:] == (Level.LINE, Level.STATION):
        return T1_TO_END if path[-1][1] == 'E1' else T1_TO
    if path == (*metro, (Level.LINE, 'm1')):
        return TOWARDS
    return None


TO_COLUMN: Final[tuple[Event, ...]] = (
    OPEN,
    ColumnLoaded(path=(), column=KINDS),
    previews_of(path=(), column=KINDS),
    DOWN,
    NEXT,
    previews_of(path=TRAIN, column=TRAIN_LINES),
    NEXT,
    KnobTurned(knob=Knob.BOTTOM, steps=3),
    NEXT,
    ColumnLoaded(path=(*T1, (Level.STATION, 'E2')), column=T1_TO),
)
TO_STAGE: Final[tuple[Event, ...]] = (
    OPEN,
    ColumnLoaded(path=(), column=KINDS),
    previews_of(path=(), column=KINDS),
    NEXT,
    previews_of(path=BUS, column=GROUPS),
    DOWN,
    NEXT,
    previews_of(path=BUS_606[:2], column=ROUTES),
    NEXT,
    NEXT,
    LineLoaded(towards=11, line=LINE),
    DOWN,
    NEXT,
)


KERB_LINE: Final[tuple[StopPlace, ...]] = (
    place('s0', name='Oakington'),
    place('s1', name='Histon'),
    place('s2', name='Orchard Close'),
    place('s3', name='Mitcham'),
    place('s4', name='Emmanuel Street'),
)
QUIET_PAIR: Final[KerbPair] = KerbPair(
    station='s2', other=place('x2', name='Orchard Close'), busy=False,
)
BUSY_PAIR: Final[KerbPair] = replace(QUIET_PAIR, busy=True)
S2A: Final[StopPlace] = place('s2a', name='Orchard Close')
S2B: Final[StopPlace] = place('s2b', name='Orchard Close Opp')
PARENT_KERBS: Final[ParentKerbs] = ParentKerbs(
    station='s2',
    kerbs=(
        ParentKerb(place=S2A, onward=frozenset({'s3'}), trips=40),
        ParentKerb(place=S2B, onward=frozenset({'s1'}), trips=30),
    ),
    busy=False,
)
PARENT_PAIR: Final[KerbPair] = KerbPair(
    station='s2', other=S2B, busy=False, here=S2A,
)


def at_kerbs(
    *,
    pairs: tuple[KerbPair, ...] = (QUIET_PAIR,),
    menu: int | None = None,
) -> ChooserState:
    """
    Open 606's first towards row on `KERB_LINE`, cursor on Orchard Close.

    Parameters
    ----------
    pairs : tuple[KerbPair, ...]
        The line's paired kerbs.
    menu : int | None
        The box's cursor row, or None for a closed box.

    Returns
    -------
    ChooserState
        The chooser in the FROM stage.
    """
    chooser, _ = pressed(chooser=at_towards(), reach=None)
    chooser = with_line(
        chooser=chooser, towards=11, line=KERB_LINE, pairs=pairs,
    )
    return replace(
        chooser,
        line_cursor=2,
        kerbs=Kerbs(pairs=pairs, menu=menu),
    )
