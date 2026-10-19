"""Views to draw in tests: a Stop view, a Strip view and chooser columns."""

from paperroute.core.chooser import Choice, Level
from paperroute.core.chooser_view import ChooserView, ColumnView
from paperroute.core.model import CarryOn, Mode
from paperroute.core.reach import RowKind
from paperroute.core.stop_view import Pill, PinTag, Row, StopView
from paperroute.core.strip_model import (
    ROW_H,
    STRIP_TOP,
    StripPanel,
    StripRow,
    StripView,
)


def stop_row(
    *,
    pill: Pill | None = None,
    destination: str = 'Parramatta',
    then: CarryOn | None = None,
) -> Row:
    """
    A row for drawing.

    Parameters
    ----------
    pill : Pill | None
        Marker.
    destination : str
        Destination text.
    then : CarryOn | None
        Where the vehicle runs on to.

    Returns
    -------
    Row
        Row.
    """
    return Row(
        route='606', mode=Mode.BUS, platform='B4', destination=destination,
        countdown='9', leaves_at='8:09', arrives_at='8:45', ride_minutes=36,
        live=True, pill=pill, then=then)


STOP_VIEW = StopView(
    origin='Hollis St Opp Constitution Hill Library', destination='Parramatta Station',
    clock='8:00', hero=stop_row(),
    rows=(stop_row(), stop_row(pill=Pill.FASTER)),
    banner=None, empty=None, scroll=0)

def panel_row(*, then: CarryOn | None = None,
              pill: Pill | None = None) -> Row:
    """
    Make a panel departure.

    Parameters
    ----------
    then : CarryOn | None
        Where the vehicle runs on to.
    pill : Pill | None
        Marker.

    Returns
    -------
    Row
        The departure.
    """
    return Row(
        route='T1', mode=Mode.TRAIN, platform='3', destination='Emu Plains',
        countdown='6', leaves_at='8:11', arrives_at='8:47', ride_minutes=36,
        live=True, pill=pill, then=then)


def strip_row(*, index: int, tag: str = '', parent: bool = True,
              cursor: bool = False) -> StripRow:
    """
    Make a main-line row in a slot.

    Parameters
    ----------
    index : int
        Slot from the top.
    tag : str
        Tag text.
    parent : bool
        Join it to the slot above.
    cursor : bool
        The row being picked.

    Returns
    -------
    StripRow
        The row.
    """
    y = STRIP_TOP + index * ROW_H + ROW_H / 2
    return StripRow(
        name=f'Station {index}', depth=0, kind=RowKind.MAIN, y=y,
        parent_y=y - ROW_H if parent else None, parent_depth=0, tag=tag,
        time='' if tag else f'8:{10 + index}', ride=False, cursor=cursor,
        bold=index == 2, station=f'S{index}')


STRIP_ROWS = tuple(
    strip_row(index=index, parent=index > 0)
    for index in range(12)
)
STRIP_PANEL = StripPanel(
    title='Town Hall', destination='Parramatta', note='201 stations from here',
    rows=(panel_row(),) * 4, no_destination=False, hint=None)
STRIP_VIEW = StripView(
    origin='Town Hall', destination='Parramatta', clock='8:05',
    tag=PinTag(text='pin 2/3', pinned=True), picking=None, rows=STRIP_ROWS,
    band=None, markers=(), panel=STRIP_PANEL, empty=None, banner=None)

def route_choice(*, label: str = 'Parramatta', tag: str = '') -> Choice:
    """
    Make a route row.

    Parameters
    ----------
    label : str
        Long name.
    tag : str
        Place tag.

    Returns
    -------
    Choice
        The row.
    """
    return Choice(key='606', label=label, badge='606', tag=tag)


def column(
    *,
    level: Level,
    items: tuple[Choice, ...],
    focused: bool = True,
    total: int | None = None,
) -> ColumnView:
    """
    Make a column whose first row is selected.

    Parameters
    ----------
    level : Level
        What it lists.
    items : tuple[Choice, ...]
        Rows.
    focused : bool
        Whether the knob moves it.
    total : int | None
        Rows in the whole column; the row count when None.

    Returns
    -------
    ColumnView
        The column.
    """
    return ColumnView(
        level=level,
        items=items,
        selected=0,
        focused=focused,
        first=0,
        total=len(items) if total is None else total,
    )


def view_of(*columns: ColumnView, empty: str | None = None) -> ChooserView:
    """
    Make a chooser view.

    Parameters
    ----------
    *columns : ColumnView
        Columns, left to right.
    empty : str | None
        Text in place of the columns.

    Returns
    -------
    ChooserView
        The view.
    """
    return ChooserView(
        title='Choose a route', trail='Bus', columns=columns, empty=empty,
    )


def groups(*, focused: bool) -> ColumnView:
    """
    Make a group column of three rows.

    Parameters
    ----------
    focused : bool
        Whether the knob moves it.

    Returns
    -------
    ColumnView
        The column.
    """
    items = tuple(
        Choice(key=label, label=label, detail='12')
        for label in ('1-99', '100s', '600s')
    )
    return column(level=Level.GROUP, items=items, focused=focused)
