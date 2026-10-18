"""Chooser columns, a strip and walks through the chooser for tests."""

from typing import Final

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
from paperroute.core.model import Pin, RouteKind, StopPlace
from paperroute.core.reach import Reach, ReachRow, RowKind
from tests.core.factories import place

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
T1_TOWARDS: Final[Column] = Column(
    level=Level.TOWARDS,
    items=(
        Choice(key='21', label='Emu Plains', detail='90'),
        Choice(
            key='replacements', label='Replacement buses',
            detail='2 routes', level=Level.REPLACEMENTS,
        ),
    ),
)
T1_BUSES: Final[Column] = Column(
    level=Level.ROUTE,
    items=(
        Choice(key='b1', label='Strathfield to Parramatta', badge='T1X'),
        Choice(key='b2', label='Penrith to Emu Plains', badge='T1Y'),
    ),
)
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


def opened_at_kind(kind: RouteKind | None = None) -> ChooserState:
    """
    Open the chooser and load the KIND column.

    Parameters
    ----------
    kind : RouteKind | None
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
