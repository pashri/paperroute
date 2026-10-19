"""The Strip and Strip + map view-models, their inputs and layout sizes."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.map_view import MapView, Marker
from paperroute.core.patterns import TripPattern
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.reach import Reach, RowKind, StripKey
from paperroute.core.stop_view import DOT, PinTag, Row, StopView

STRIP_TOP: Final[int] = 44
ROW_H: Final[int] = 36
STRIP_ROWS: Final[int] = 12
TRACK_X: Final[int] = 30
INDENT: Final[int] = 16
PANE_TRACK_X: Final[int] = 500
PANE_ROWS: Final[int] = 12
PANE_FROM_ROW: Final[int] = 2
PANEL_ROWS: Final[int] = 4
BANNER_ROWS: Final[int] = 1  # row slots the live data banner covers
CURSOR_MARGIN: Final[int] = 2
LOADING: Final[str] = 'Loading the line…'
NO_STRIP: Final[str] = 'No strip for this stop'
NEEDS_UPDATE: Final[str] = 'Strip needs a timetable update'
HINTS: Final[dict[str, str]] = {
    'TO': f'Press: set TO{DOT}press FROM: all departures',
    'FROM': f'Press: set FROM{DOT}top press: back',
}
PICK_FROM: Final[str] = 'FROM'
PICK_TO: Final[str] = 'TO'
ALL_TAG: Final[str] = 'ALL'  # the From row while picking TO
EMPTY_STOP_VIEW: Final[StopView] = StopView(
    origin='',
    destination=None,
    clock='',
    hero=None,
    rows=(),
    banner=None,
    empty=None,
    scroll=0,
)


@dataclass(frozen=True)
class StripRow:
    """
    One station as the strip draws it.

    Attributes
    ----------
    name : str
        Station name, without ' Station'.
    depth : int
        Indent: 0 for the main line, one more per branch level.
    kind : RowKind
        Approach, the From, the main line or a branch.
    y : float
        Screen y of the row's centre line.
    parent_y : float | None
        Screen y of the row the track comes from, which may be off the
        strip; None for the From.
    parent_depth : int
        Depth of that row; the row's own depth when it has none.
    tag : str
        'TO?' or 'FROM?' on the cursor while picking (ALL_TAG on the From
        while picking TO), else ''.
    time : str
        Clock time of the next departure on the From, of the first
        arrival elsewhere, or ''.
    ride : bool
        True on the ridden rows, From to Destination.
    cursor : bool
        True on the row being picked.
    bold : bool
        True for the From and the Destination.
    station : str
        The row's station ID.
    name_after : float | None
        The x the name starts at or right of, past badges beside the
        row's disc; None for the usual place.
    """

    name: str
    depth: int
    kind: RowKind
    y: float
    parent_y: float | None
    parent_depth: int
    tag: str
    time: str
    ride: bool
    cursor: bool
    bold: bool
    station: str
    name_after: float | None = None


@dataclass(frozen=True)
class Band:
    """
    The band standing in for a From scrolled off the strip.

    Attributes
    ----------
    text : str
        For example 'FROM Town Hall · 3 stops away'.
    top : bool
        True in the top slot, False in the bottom one.
    y : float
        Screen y of the slot's top edge.
    """

    text: str
    top: bool
    y: float


@dataclass(frozen=True)
class Window:
    """
    Where a strip's row slots sit on screen.

    Attributes
    ----------
    top : int
        Screen y of the top slot's top edge.
    rows : int
        How many slots there are.
    """

    top: int
    rows: int


@dataclass(frozen=True)
class StripPanel:
    """
    The departures panel beside the strip.

    Attributes
    ----------
    title : str
        The From name.
    destination : str | None
        The Destination's name, drawn after an arrow, or None when the
        place has no destination.
    note : str
        How many stations the strip reaches.
    rows : tuple[Row, ...]
        Departures to show; each `Row.then` is drawn as a second line.
    no_destination : bool
        True when the rows are the whole-stop board.
    hint : str | None
        The knob hint while picking, else None.
    empty : str | None
        Why there are no departures to list, or None.
    """

    title: str
    destination: str | None
    note: str
    rows: tuple[Row, ...]
    no_destination: bool
    hint: str | None
    empty: str | None = None


@dataclass(frozen=True)
class StripView:
    """
    Everything the Strip view draws.

    Attributes
    ----------
    origin : str
        From name.
    destination : str | None
        Destination name, or None.
    clock : str
        Current time.
    tag : PinTag
        The header's pin tag.
    picking : str | None
        'TO' or 'FROM' while the chooser picks a stop, else None.
    rows : tuple[StripRow, ...]
        Visible rows, top to bottom.
    band : Band | None
        The From band, when the From is off the strip.
    markers : tuple[Marker, ...]
        Vehicles on the strip.
    panel : StripPanel
        The departures panel.
    empty : str | None
        Why there is no strip, or None.
    banner : str | None
        Live data banner, or None.
    """

    origin: str
    destination: str | None
    clock: str
    tag: PinTag
    picking: str | None
    rows: tuple[StripRow, ...]
    band: Band | None
    markers: tuple[Marker, ...]
    panel: StripPanel
    empty: str | None
    banner: str | None


@dataclass(frozen=True)
class StripPane:
    """
    The strip beside the map on Strip + map.

    Attributes
    ----------
    rows : tuple[StripRow, ...]
        Visible rows, top to bottom.
    markers : tuple[Marker, ...]
        Vehicles on the strip.
    """

    rows: tuple[StripRow, ...]
    markers: tuple[Marker, ...]


@dataclass(frozen=True)
class StripMapView:
    """
    Everything the Strip + map view draws.

    Attributes
    ----------
    map : MapView
        The map at the left.
    pane : StripPane
        The strip at the right.
    """

    map: MapView
    pane: StripPane


@dataclass(frozen=True)
class StripInputs:
    """
    Everything the Strip views are built from.

    Attributes
    ----------
    reach : Reach | None
        The strip's rows, or None while loading or unavailable.
    unavailable : str | None
        Why there is no reach, or None.
    departures : tuple[LiveDeparture, ...]
        Every departure from the From, with live times applied.
    patterns : Mapping[str, TripPattern | None]
        Loaded trip patterns by trip ID; None when unknown.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed in use.
    now : datetime
        Current time.
    scroll : int
        Rows scrolled down from the default.
    cursor : int | None
        Reach row being picked, or None.
    picking : str | None
        'TO' or 'FROM' while the chooser picks a stop, else None.
    destination : str | None
        Destination stop or station ID, or None.
    tag : PinTag
        The header's pin tag.
    stop_view : StopView
        The place's unscrolled Stop view, for the header and panel.
    key : StripKey | None
        Which runs feed the strip; None for no departures.
    """

    reach: Reach | None
    unavailable: str | None
    departures: tuple[LiveDeparture, ...]
    patterns: Mapping[str, TripPattern | None]
    positions: Mapping[str, PositionsSnapshot]
    now: datetime
    scroll: int
    cursor: int | None
    picking: str | None
    destination: str | None
    tag: PinTag
    stop_view: StopView = EMPTY_STOP_VIEW
    key: StripKey | None = None


@dataclass(frozen=True)
class Marks:
    """
    What the rows of a reach are marked with.

    Attributes
    ----------
    origin : int | None
        The From row, if any.
    target : int | None
        The Destination row, if it is on the strip.
    ride : frozenset[int]
        Rows from the From to the Destination.
    times : Mapping[int, str]
        Clock time by row.
    cursor : int | None
        The row being picked.
    picking : str | None
        'TO' or 'FROM' while picking.
    """

    origin: int | None
    target: int | None
    ride: frozenset[int]
    times: Mapping[int, str]
    cursor: int | None
    picking: str | None
