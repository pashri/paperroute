"""Golden scene constants and the helpers that draw a frame."""

import os
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, cast

import numpy as np
from numpy.typing import NDArray

from paperroute_cambridge.city import CAMBRIDGE, CAMBRIDGE_ZONE
from paperroute_device.core.banners import banner_line
from paperroute_device.core.panel_screens import panel_view
from paperroute_device.runtime.outputs import drawing
from paperroute_sydney.city import SYDNEY, SydneyFeed, SydneyMode
from paperroute_testkit.core.connection_factories import (
    PARRAMATTA,
    TWO_WAY,
    WORK,
)
from paperroute_testkit.core.factories import EAST, START, departure
from paperroute_testkit.core.kit_steps import QUIET_PARTS, device_of
from paperroute_testkit.core.strip_factories import SOUTH, WEST
from paperroute_transit.app_render import MapRenderer
from paperroute_transit.core.messages import ScreenView
from paperroute_transit.core.model import Departure, Pin, StopPlace
from paperroute_transit.core.sleep import sleep_view
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import banner_live_view, current_view
from paperroute_transit.core.tiles import (
    TILE,
    LabelKind,
    StreetLabel,
    TileVariant,
)
from paperroute_transit.transit_app import TransitApp
from tests.core.state.helpers import CENTRE

GOLDEN: Final[Path] = Path(__file__).parents[1]


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


APPROACH: Final[dict[str, tuple[float, float]]] = {
    'W2': (-18_200.0, 1_600.0),
    'W1': (-17_800.0, 1_400.0),
    'W0': (-17_400.0, 1_200.0),
    'C': CENTRE,
    'E1': ROUTE[1],
    'E2': ROUTE[2],
}


LABEL: Final[str] = 'Golden St'


DAY_STOPS: Final[dict[str, tuple[float, float]]] = {
    'N2': (-17_000.0, 2_000.0),
    'N1': (-17_000.0, 1_600.0),
    'C': CENTRE,
    'S1': (-17_000.0, 500.0),
    'S2': (-17_000.0, 100.0),
}


NORTH_EAST: Final[dict[str, tuple[float, float]]] = {
    'C': CENTRE,
    'B1': (-16_600.0, 1_500.0),
    'B2': (-16_100.0, 2_000.0),
}


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


APPS: Final[dict[str, TransitApp]] = {
    city.name: TransitApp(city=city, parts=QUIET_PARTS)
    for city in (SYDNEY, CAMBRIDGE)
}


CAMBRIDGE_AT: Final[datetime] = datetime(
    2026, 11, 16, 7, 0, tzinfo=CAMBRIDGE_ZONE,
)


CAMBRIDGE_TO_ELY: Final[Pin] = Pin(
    origin=StopPlace(stop_id='rail:CBG', name='Cambridge', point=None),
    destination=StopPlace(stop_id='rail:ELY', name='Ely', point=None),
    kind='train',
)


# Minutes after CAMBRIDGE_AT, minutes to Ely, badge, headsign, platform.
ELY_TRAINS: Final[tuple[tuple[int, int, str, str, str], ...]] = (
    (6, 16, 'XC', 'Birmingham New Street', '4'),
    (21, 17, 'GN', 'Kings Lynn', '4'),
    (33, 18, 'GA', 'Norwich', '5'),
    (51, 17, 'GN', 'Kings Lynn', '1'),
)


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
        leaves it on screen; else the kit's panel screen, the sleep
        screen or the live view under the banner line, held or not.
    """
    if state.power is not None and state.last_view is not None:
        return state.last_view
    app, device = APPS[state.city.name], device_of(state=state)
    overlay = panel_view(
        device=device, app=app, state=state, zone=state.city.zone,
    )
    if overlay is not None:
        return cast(ScreenView, overlay)
    banner = banner_line(
        panel=state.panel, app=app, state=state, device=device,
    )
    stop_view = banner_live_view(state=state, banner=banner)
    if state.panel.asleep:
        return sleep_view(state=state, stop_view=stop_view)
    return current_view(state=state, stop_view=stop_view)


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
    app = TransitApp(city=state.city, parts=QUIET_PARTS)
    app.maps = MapRenderer(tiles=GoldenTiles())
    return drawing(app=app, view=view_of(state=state))()


def bus_departures(
    *, ride: int = 30, platform: str = '',
) -> tuple[Departure, ...]:
    """
    Three bus departures at 3, 6 and 9 minutes past the start.

    Parameters
    ----------
    ride : int
        Minutes to the destination.
    platform : str
        Platform label.

    Returns
    -------
    tuple[Departure, ...]
        The departures.
    """
    return tuple(
        departure(
            departs=START + timedelta(minutes=minutes),
            mode=SydneyFeed.BUS,
            kind=SydneyMode.BUS,
            ride=ride,
            platform=platform,
        )
        for minutes in (3, 6, 9)
    )
