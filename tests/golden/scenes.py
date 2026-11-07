"""Fixed states, one for each kind of screen, and how they are drawn."""

import os
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.app_render import MapRenderer, drawing
from paperroute.core.chains import Legs, leg_key
from paperroute.core.connection_model import way_legs
from paperroute.core.device_model import (
    Confirm,
    ConfirmAction,
    Menu,
    Origin,
    PanelScreen,
    WifiPhase,
    WifiSetup,
)
from paperroute.core.geo import make_shape
from paperroute.core.messages import (
    DeparturesLoaded,
    FetchFeed,
    PatternsLoaded,
    PositionsFetched,
    PowerAction,
    PowerRequested,
    ScreenView,
    ShapesLoaded,
    StripLoaded,
)
from paperroute.core.model import Pin
from paperroute.core.patterns import TripPattern
from paperroute.core.saved import LiveView
from paperroute.core.state import step
from paperroute.core.state_model import State
from paperroute.core.state_views import current_view, live_stop_view
from paperroute.core.tiles import TILE, LabelKind, StreetLabel, TileVariant
from tests.adapters.test_web_device import FACTS
from tests.core.chooser_walks import at_towards
from tests.core.connection_factories import (
    LEG1,
    LEG2,
    PARRAMATTA,
    TWO_WAY,
    WORK,
    at,
)
from tests.core.factories import (
    EAST,
    START,
    example_reach,
    line_pattern,
    snapshot,
    train,
    vehicle,
)
from tests.core.steps import answer_fetches
from tests.core.strip_factories import KEY, PIN, SOUTH, WEST, on_view
from tests.core.test_alerts_view import on_alerts
from tests.core.test_chains import TRAINS
from tests.core.test_connection_screens import started, with_legs
from tests.core.test_connection_strip import PATTERNS as LEG_PATTERNS
from tests.core.test_state import BUS_POSITIONS, CENTRE, PRESS, fresh, loaded

GOLDEN: Final[Path] = Path(__file__).parent
UPDATING: Final[bool] = os.getenv('GOLDEN_UPDATE') == '1'
TWO_CHANGES: Final[Pin] = replace(
    WORK, destination=PARRAMATTA, ways=(TWO_WAY,),
)
ROUTE: Final[list[tuple[float, float]]] = [
    CENTRE,
    (-16_200.0, 1_000.0),
    (-15_600.0, 300.0),
    (-15_000.0, -400.0),
]
LABEL: Final[str] = 'Golden St'
STRIP_TRIPS: Final[dict[str, tuple[tuple[str, ...], int]]] = {
    'e0': (EAST, -2),
    'e': (EAST, 0),
    'w': (WEST, 3),
    'r': (SOUTH, 4),
}


def baseline_bytes(*, path: Path, data: bytes) -> bytes:
    """
    Read a baseline file, first writing it when updating baselines.

    Parameters
    ----------
    path : Path
        The baseline file.
    data : bytes
        What the code gives today.

    Returns
    -------
    bytes
        The file's contents; `data` itself when updating.
    """
    if UPDATING:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path.read_bytes()


class GoldenTiles:
    """Tiles drawn from their own address, with one street name."""

    def tile(
        self, *, zoom: int, col: int, row: int, variant: TileVariant
    ) -> NDArray[np.uint8]:
        """
        Draw one tile from its zoom, column and row.

        Parameters
        ----------
        zoom : int
            Metres per pixel.
        col : int
            Tile column.
        row : int
            Tile row.
        variant : TileVariant
            Ignored.

        Returns
        -------
        NDArray[np.uint8]
            White or light grey by the tile's parity, with a dark line
            whose place follows the address.
        """
        del variant
        pixels = np.full((TILE, TILE), (255, 170)[(col + row) % 2], np.uint8)
        pixels[(zoom + col * 7 + row * 13) % TILE, :] = 85
        pixels[:, (zoom * 3 + col * 11 + row) % TILE] = 85
        return pixels

    def street_labels(
        self,
        *,
        zoom: int,
        box: tuple[float, float, float, float],
        kind: LabelKind = LabelKind.STREET,
    ) -> list[StreetLabel]:
        """
        Give one street name across the middle of the box.

        Parameters
        ----------
        zoom : int
            Ignored.
        box : tuple[float, float, float, float]
            Left, top, right and bottom in global pixels.
        kind : LabelKind
            Which labels; only street names are given.

        Returns
        -------
        list[StreetLabel]
            One label for street names; none for other kinds.
        """
        del zoom
        if kind is not LabelKind.STREET:
            return []
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return [StreetLabel(
            name=LABEL, x=x + 60.0, y=y, angle=0.0, width=80.0,
            start=(x, y), end=(x + 120.0, y),
        )]

    def version(self) -> str:
        """
        Say which build the tiles are.

        Returns
        -------
        str
            Always 'golden'.
        """
        return 'golden'


def view_of(*, state: State) -> ScreenView:
    """
    Build the view a state shows.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    ScreenView
        The power notice while a power action is under way, as the app
        leaves it on screen; otherwise the current view.
    """
    if state.power is not None and state.last_view is not None:
        return state.last_view
    return current_view(state=state, stop_view=live_stop_view(state=state))


def frame_of(*, state: State) -> NDArray[np.uint8]:
    """
    Draw the view a state shows, over the golden tiles.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 grey pixels.
    """
    maps = MapRenderer(tiles=GoldenTiles())
    return drawing(view=view_of(state=state), maps=maps)()


def stop() -> State:
    """
    Show three buses on the Stop view.

    Returns
    -------
    State
        State at `START`.
    """
    return loaded(state=fresh(centre=CENTRE))


def stop_larger_inverted() -> State:
    """
    Show the Stop view in larger text with inverted lists.

    Returns
    -------
    State
        State at `START`.
    """
    state = stop()
    settings = replace(state.settings, larger_text=True, inverted_lists=True)
    return replace(state, settings=settings)


def stop_no_key() -> State:
    """
    Show the Stop view with no API key set.

    Returns
    -------
    State
        State at `START`.
    """
    state = stop()
    return replace(state, panel=replace(state.panel, key_set=False))


def map_scene() -> State:
    """
    Show the Map with a route line and a live bus.

    Returns
    -------
    State
        State at `START`.
    """
    state, effects = step(
        loaded(state=fresh(centre=CENTRE), shaped=True), PRESS,
    )
    others = tuple(
        effect
        for effect in effects
        if isinstance(effect, FetchFeed) and effect.feed != BUS_POSITIONS
    )
    state, _ = answer_fetches(state=state, effects=others)
    line = make_shape(shape_id='b:0', points=ROUTE)
    state, _ = step(state, ShapesLoaded(shapes={'b:0': line}))
    bus = vehicle(timestamp=int(START.timestamp()), point=ROUTE[1])
    positions = snapshot(fetched_at=START, vehicles=(bus,))
    state, _ = step(
        state, PositionsFetched(feed=BUS_POSITIONS, snapshot=positions),
    )
    return state


def map_panes() -> State:
    """
    Show a two-change connection pin's Map, one pane per change.

    Returns
    -------
    State
        State at 7:30 on the connection's day.
    """
    state = started(view=LiveView.MAP, pins=(TWO_CHANGES,))
    lists = (LEG1, LEG2, TRAINS)
    legs = way_legs(pin=TWO_CHANGES, way=TWO_WAY)
    state, _ = step(state, DeparturesLoaded(
        pin=TWO_CHANGES,
        departures=(),
        until=at(10, 30),
        legs=Legs(lists=MappingProxyType({
            leg_key(leg=leg): items for leg, items in zip(legs, lists)
        })),
    ))
    return state


def strip_scene(*, view: LiveView) -> State:
    """
    Show trains on a branching line on a Strip view.

    Parameters
    ----------
    view : LiveView
        The Strip or Strip + map.

    Returns
    -------
    State
        State at `START`, with the reach and every train's pattern.
    """
    departures = tuple(
        train(minutes=minutes, trip_id=trip)
        for trip, (_, minutes) in STRIP_TRIPS.items()
    )
    state, _ = on_view(
        view=view, pin=replace(PIN, destination=None), departures=departures,
    )
    state, _ = step(state, StripLoaded(key=KEY, reach=example_reach()))
    patterns: dict[str, TripPattern | None] = {
        trip: line_pattern(trip_id=trip, stations=line, minutes=minutes)
        for trip, (line, minutes) in STRIP_TRIPS.items()
    }
    return patterns_loaded(state=state, patterns=patterns)


def patterns_loaded(
    *, state: State, patterns: dict[str, TripPattern | None],
) -> State:
    """
    Load some trips' patterns.

    Parameters
    ----------
    state : State
        State.
    patterns : dict[str, TripPattern | None]
        Patterns by trip ID.

    Returns
    -------
    State
        State after the load.
    """
    state, _ = step(state, PatternsLoaded(
        trip_ids=frozenset(patterns), patterns=patterns, shapes={},
    ))
    return state


def connection_strip(*, view: LiveView) -> State:
    """
    Show the Work connection pin's legs on a Strip view.

    Parameters
    ----------
    view : LiveView
        The Strip or Strip + map.

    Returns
    -------
    State
        State at 7:30 on the connection's day, with both legs' patterns.
    """
    return patterns_loaded(
        state=connection(view=view), patterns=dict(LEG_PATTERNS),
    )


def connection(*, view: LiveView) -> State:
    """
    Show the Work connection pin on a Live view.

    Parameters
    ----------
    view : LiveView
        The Live view.

    Returns
    -------
    State
        State at 7:30 on the connection's day.
    """
    return with_legs(state=started(view=view))


def chooser() -> State:
    """
    Show the chooser at 606's Towards column.

    Returns
    -------
    State
        State at `START`.
    """
    return replace(stop(), chooser=at_towards())


def building() -> State:
    """
    Show the Building screen, with no timetable.

    Returns
    -------
    State
        State at `START`.
    """
    state = fresh()
    return replace(state, rebuild=replace(state.rebuild, missing=True))


def notice() -> State:
    """
    Show the notice while shutting down.

    Returns
    -------
    State
        State at `START`, shutting down.
    """
    state, _ = step(stop(), PowerRequested(action=PowerAction.OFF))
    return state


def panel(
    *,
    menu: Menu | None = None,
    wifi: WifiSetup | None = None,
    confirm: Confirm | None = None,
) -> State:
    """
    Show a panel screen over the Stop view.

    Parameters
    ----------
    menu : Menu | None
        The open menu.
    wifi : WifiSetup | None
        The open Wi-Fi setup.
    confirm : Confirm | None
        The question asked.

    Returns
    -------
    State
        State at `START`, with the probe's facts.
    """
    state = stop()
    shown = replace(
        state.panel, facts=FACTS, menu=menu, wifi=wifi, confirm=confirm,
    )
    return replace(state, panel=shown)


def asleep() -> State:
    """
    Show the sleep screen over the Map scene's pin.

    Returns
    -------
    State
        State at `START`, asleep.
    """
    state = map_scene()
    return replace(state, panel=replace(state.panel, asleep=True))


SCENES: Final[dict[str, Callable[[], State]]] = {
    'stop': stop,
    'stop_larger_inverted': stop_larger_inverted,
    'stop_no_key': stop_no_key,
    'map': map_scene,
    'map_panes': map_panes,
    'strip': lambda: strip_scene(view=LiveView.STRIP),
    'strip_map': lambda: strip_scene(view=LiveView.STRIP_MAP),
    'alerts': on_alerts,
    'connection': lambda: connection(view=LiveView.STOP),
    'connection_strip': lambda: connection_strip(view=LiveView.STRIP),
    'connection_strip_map': lambda: connection_strip(
        view=LiveView.STRIP_MAP,
    ),
    'chooser': chooser,
    'building': building,
    'notice': notice,
    'menu': lambda: panel(menu=Menu()),
    'device': lambda: panel(menu=Menu(screen=PanelScreen.DEVICE)),
    'phone': lambda: panel(menu=Menu(screen=PanelScreen.PHONE)),
    'wifi_setup': lambda: panel(wifi=WifiSetup(
        phase=WifiPhase.HOTSPOT,
        password='7Q4K9W2M',
        until=START + timedelta(minutes=10),
    )),
    'confirm': lambda: panel(confirm=Confirm(
        action=ConfirmAction.SHUT_DOWN, origin=Origin.PANEL, asked_at=START,
    )),
    'sleep': asleep,
}
