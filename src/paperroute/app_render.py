"""Draw views into frames and show them."""

import asyncio
import functools
import logging
import sqlite3
from collections.abc import Callable
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.app_effects import timed_async
from paperroute.app_ports import Ports
from paperroute.render.alerts_view import draw_alerts_view
from paperroute.render.chooser import draw_chooser_view
from paperroute.render.connection_strip import draw_connection_strip
from paperroute.render.connection_strip_map import draw_connection_strip_map
from paperroute.render.connection_view import draw_connection_view
from paperroute.render.keep_out import KeepOut, keep_out
from paperroute.render.map_base import draw_map_base
from paperroute.render.map_panes import draw_map_panes
from paperroute.render.map_view import draw_map_view
from paperroute.render.stop_view import draw_stop_view
from paperroute.render.strip_view import draw_strip_map_view, draw_strip_view
from paperroute_device.core.panel_views import (
    ConfirmView,
    DeviceView,
    MenuView,
    NoticeView,
    PhoneView,
    WifiSetupView,
)
from paperroute_device.render.notice import draw_notice_view
from paperroute_device.render.panel import draw_panel_view
from paperroute_device.render.sleep import with_sleep_band
from paperroute_hardware.ports import CLEAN_AFTER
from paperroute_transit.adapters.tiles_db import NullTiles
from paperroute_transit.core.alerts_view import AlertsView
from paperroute_transit.core.chooser_view import ChooserView
from paperroute_transit.core.connection_strip import (
    ConnectionStripMapView,
    ConnectionStripView,
)
from paperroute_transit.core.connection_view import ConnectionView
from paperroute_transit.core.map_view import MapBase, MapView
from paperroute_transit.core.messages import ScreenView
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.stop_view import StopView
from paperroute_transit.core.strip_model import StripMapView, StripView
from paperroute_transit.core.tiles import TileVariant
from paperroute_transit.ports import TileRepository

logger = logging.getLogger(__name__)

KEPT_BOXES: Final[int] = 4  # maps whose keep-out boxes are remembered


type View = ScreenView


type Frame = NDArray[np.uint8]


type PlainView = (
    StopView
    | ConnectionView
    | ConnectionStripView
    | StripView
    | AlertsView
    | ChooserView
    | NoticeView
    | MenuView
    | DeviceView
    | PhoneView
    | WifiSetupView
    | ConfirmView
)


class MapRenderer:
    """
    Draw Map view frames, keeping the last map base for the next frame.

    A tiles file that fails a read is drawn as no tiles at all.

    Parameters
    ----------
    tiles : TileRepository
        Where the tiles come from.
    variant : TileVariant
        Which tile rendering to draw.
    """

    def __init__(
        self,
        *,
        tiles: TileRepository,
        variant: TileVariant = TileVariant.GREY,
    ) -> None:
        self.tiles = tiles
        self.variant = variant
        self.key: tuple[MapBase, str | None] | None = None
        self.base: NDArray[np.uint8] | None = None
        self.unusable: set[str | None] = set()
        self.side: dict[tuple[MapBase, str | None], NDArray[np.uint8]] = {}
        self.kept: dict[tuple[MapBase, str | None], KeepOut] = {}

    def frame(self, *, view: MapView) -> NDArray[np.uint8]:
        """
        Draw one frame, drawing the map base again only when it changed.

        Parameters
        ----------
        view : MapView
            View to draw.

        Returns
        -------
        NDArray[np.uint8]
            480 by 800 pixels.
        """
        if view.panes:
            return draw_map_panes(
                view=view,
                bases=(
                    self.cached(base=view.base),
                    *(self.side_cached(base=pane.base) for pane in view.panes),
                ),
                keep_outs=[
                    self.boxes(base=pane.base)
                    for pane in (view, *view.panes)
                ],
            )
        return draw_map_view(
            view=view,
            base=self.cached(base=view.base),
            keep_out=self.boxes(base=view.base),
        )

    def strip_map_frame(self, *, view: StripMapView) -> NDArray[np.uint8]:
        """
        Draw one Strip + map frame, sharing the cached map base.

        Parameters
        ----------
        view : StripMapView
            View to draw.

        Returns
        -------
        NDArray[np.uint8]
            480 by 800 pixels.
        """
        return draw_strip_map_view(
            view=view,
            base=self.cached(base=view.map.base),
            keep_out=self.boxes(base=view.map.base),
        )

    def connection_strip_map_frame(
        self, *, view: ConnectionStripMapView
    ) -> NDArray[np.uint8]:
        """
        Draw one connection Strip + map frame, sharing the cached map base.

        Parameters
        ----------
        view : ConnectionStripMapView
            View to draw.

        Returns
        -------
        NDArray[np.uint8]
            480 by 800 pixels.
        """
        return draw_connection_strip_map(
            view=view,
            base=self.cached(base=view.map.base),
            keep_out=self.boxes(base=view.map.base),
        )

    def cached(self, *, base: MapBase) -> NDArray[np.uint8]:
        """
        Give the map base, drawing it again only when it changed.

        Parameters
        ----------
        base : MapBase
            The base map key, which carries the width.

        Returns
        -------
        NDArray[np.uint8]
            The map base for the installed tiles.
        """
        key = (base, self.version())
        if self.base is None or key != self.key:
            self.base = self.drawn_base(base=base, version=key[1])
            self.key = key
        return self.base

    def side_cached(self, *, base: MapBase) -> NDArray[np.uint8]:
        """
        Give a side pane's map base, keeping only the last one drawn.

        Parameters
        ----------
        base : MapBase
            The pane's base map key.

        Returns
        -------
        NDArray[np.uint8]
            The map base for the installed tiles.
        """
        key = (base, self.version())
        if key not in self.side:
            self.side = {key: self.drawn_base(base=base, version=key[1])}
        return self.side[key]

    def boxes(self, *, base: MapBase) -> KeepOut:
        """
        Give a map's keep-out boxes, reading the labels only when it changed.

        Parameters
        ----------
        base : MapBase
            The base map key.

        Returns
        -------
        KeepOut
            The boxes for the installed tiles; only the corner boxes drawn
            without tiles when the tiles can't be read.
        """
        key = (base, self.version())
        if key not in self.kept:
            if len(self.kept) >= KEPT_BOXES:
                self.kept.clear()
            self.kept[key] = self.read_boxes(base=base)
        return self.kept[key]

    def read_boxes(self, *, base: MapBase) -> KeepOut:
        """
        Read a map's keep-out boxes, without labels when that fails.

        Parameters
        ----------
        base : MapBase
            The base map key.

        Returns
        -------
        KeepOut
            The boxes; a failed read gives the boxes of a map drawn
            without tiles, as the base is.
        """
        try:
            return keep_out(base=base, tiles=self.tiles)
        except sqlite3.Error:
            return keep_out(base=base, tiles=NullTiles())

    def version(self) -> str | None:
        """
        Say which tiles build is installed.

        Returns
        -------
        str | None
            The build time, or None when there is none or it can't be read.
        """
        try:
            return self.tiles.version()
        except sqlite3.Error:
            return None

    def drawn_base(
        self, *, base: MapBase, version: str | None
    ) -> NDArray[np.uint8]:
        """
        Draw the map base, without tiles when the tiles can't be read.

        A read error is logged once for each tiles build.

        Parameters
        ----------
        base : MapBase
            The base map key.
        version : str | None
            The tiles build drawn from.

        Returns
        -------
        NDArray[np.uint8]
            The map base; it says "Map not installed" after a read error.
        """
        try:
            return draw_map_base(
                base=base, tiles=self.tiles, variant=self.variant
            )
        except sqlite3.Error:
            if version not in self.unusable:
                logger.warning('tiles file unusable', exc_info=True)
                self.unusable.add(version)
        return draw_map_base(
            base=base, tiles=NullTiles(), variant=self.variant
        )


def sleep_frame(*, view: SleepView, maps: MapRenderer) -> Frame:
    """
    Draw the sleep screen: the map under the sleep band.

    Parameters
    ----------
    view : SleepView
        The sleep screen.
    maps : MapRenderer
        Draws the map.

    Returns
    -------
    Frame
        480 by 800 pixels, 0 or 255.
    """
    return with_sleep_band(frame=maps.frame(view=view.map), band=view.band)


def map_drawing(
    *,
    view: MapView | StripMapView | ConnectionStripMapView | SleepView,
    maps: MapRenderer,
) -> Callable[[], Frame]:
    """
    Pick the drawing for a view with a map.

    Parameters
    ----------
    view : MapView | StripMapView | ConnectionStripMapView | SleepView
        View to draw.
    maps : MapRenderer
        Draws the map.

    Returns
    -------
    Callable[[], Frame]
        Draws the frame when called.
    """
    match view:
        case MapView():
            return functools.partial(maps.frame, view=view)
        case StripMapView():
            return functools.partial(maps.strip_map_frame, view=view)
        case ConnectionStripMapView():
            return functools.partial(
                maps.connection_strip_map_frame, view=view,
            )
    return functools.partial(sleep_frame, view=view, maps=maps)


def drawing(*, view: View, maps: MapRenderer) -> Callable[[], Frame]:
    """
    Pick the drawing for a view.

    Parameters
    ----------
    view : View
        View to draw.
    maps : MapRenderer
        Draws the views with a map.

    Returns
    -------
    Callable[[], Frame]
        Draws the frame when called.
    """
    match view:
        case (
            MapView()
            | StripMapView()
            | ConnectionStripMapView()
            | SleepView()
        ):
            return map_drawing(view=view, maps=maps)
    return plain_drawing(view=view)


def stop_drawing(
    *, view: StopView | ConnectionView | ConnectionStripView
) -> Callable[[], Frame]:
    """
    Pick the drawing for a Stop view or a connection pin's Strip.

    Parameters
    ----------
    view : StopView | ConnectionView | ConnectionStripView
        View to draw.

    Returns
    -------
    Callable[[], Frame]
        Draws the frame when called.
    """
    match view:
        case StopView():
            return functools.partial(draw_stop_view, view=view)
        case ConnectionView():
            return functools.partial(draw_connection_view, view=view)
    return functools.partial(draw_connection_strip, view=view)


def plain_drawing(*, view: PlainView) -> Callable[[], Frame]:
    """
    Pick the drawing for a view without a map.

    Parameters
    ----------
    view : View
        View to draw.

    Returns
    -------
    Callable[[], Frame]
        Draws the frame when called.
    """
    match view:
        case StopView() | ConnectionView() | ConnectionStripView():
            return stop_drawing(view=view)
        case StripView():
            return functools.partial(draw_strip_view, view=view)
        case AlertsView():
            return functools.partial(draw_alerts_view, view=view)
        case NoticeView():
            return functools.partial(draw_notice_view, view=view)
        case (
            MenuView()
            | DeviceView()
            | PhoneView()
            | WifiSetupView()
            | ConfirmView()
        ):
            return functools.partial(draw_panel_view, view=view)
    return functools.partial(draw_chooser_view, view=view)


async def render(  # pylint: disable=too-many-arguments
    *,
    ports: Ports,
    view: View,
    maps: MapRenderer,
    full: bool = False,
    turned: bool = False,
    quick: bool = False,
    asleep: bool = False,
    clear: bool = False,
    clean_after: int = CLEAN_AFTER,
) -> None:
    """
    Draw a view off the event loop, show it and report how long it took.

    Parameters
    ----------
    ports : Ports
        Adapters.
    view : View
        View to draw.
    maps : MapRenderer
        Draws the views with a map.
    full : bool
        Whether to ask for a full refresh.
    turned : bool
        Whether to ask for the frame upside down.
    quick : bool
        Whether a knob was turned just now.
    asleep : bool
        Whether the device is in Sleep mode.
    clear : bool
        Whether to ask for a clear refresh.
    clean_after : int
        Quick patches before the panel cleans the screen.
    """
    draw = drawing(view=view, maps=maps)
    frame, render_ms = await timed_async(call=asyncio.to_thread(draw))
    ports.display.show(
        frame=frame,
        full=full,
        clear=clear,
        turned=turned,
        quick=quick,
        asleep=asleep,
        clean_after=clean_after,
    )
    logger.debug(
        'frame drawn', extra={'render_ms': render_ms, 'full': full}
    )
    ports.observer.frame_drawn(render_ms=render_ms)
