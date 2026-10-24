"""Draw views into frames and show them."""

import asyncio
import functools
import logging
import sqlite3
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.tiles_db import NullTiles
from paperroute.app_effects import timed_async
from paperroute.app_ports import Ports
from paperroute.core.alerts_view import AlertsView
from paperroute.core.map_view import MapBase, MapView
from paperroute.core.messages import NoticeView, ScreenView
from paperroute.core.panel_views import (
    ConfirmView,
    DeviceView,
    MenuView,
    PhoneView,
    SleepView,
    WifiSetupView,
)
from paperroute.core.stop_view import StopView
from paperroute.core.strip_model import StripMapView, StripView
from paperroute.core.tiles import TileVariant
from paperroute.ports import TileRepository
from paperroute.render.chooser import draw_chooser_view
from paperroute.render.map_base import draw_map_base
from paperroute.render.map_view import draw_map_view
from paperroute.render.notice import draw_notice_view
from paperroute.render.panel import draw_panel_view
from paperroute.render.sleep import with_sleep_band
from paperroute.render.stop_view import draw_stop_view
from paperroute.render.strip_view import draw_strip_map_view, draw_strip_view

logger = logging.getLogger(__name__)


type View = ScreenView


type Frame = NDArray[np.uint8]


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
        return draw_map_view(view=view, base=self.cached(base=view.base))

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
            view=view, base=self.cached(base=view.map.base),
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
    view: MapView | StripMapView | SleepView,
    maps: MapRenderer,
) -> Callable[[], Frame]:
    """
    Pick the drawing for a view with a map.

    Parameters
    ----------
    view : MapView | StripMapView | SleepView
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
        case MapView() | StripMapView() | SleepView():
            return map_drawing(view=view, maps=maps)
        case StopView():
            return functools.partial(draw_stop_view, view=view)
        case StripView():
            return functools.partial(draw_strip_view, view=view)
        case NoticeView() | AlertsView():
            notice = (
                view
                if isinstance(view, NoticeView)
                else NoticeView(title=view.text.title)
            )
            return functools.partial(draw_notice_view, view=notice)
        case (
            MenuView()
            | DeviceView()
            | PhoneView()
            | WifiSetupView()
            | ConfirmView()
        ):
            return functools.partial(draw_panel_view, view=view)
    return functools.partial(draw_chooser_view, view=view)


async def render(
    *,
    ports: Ports,
    view: View,
    maps: MapRenderer,
    full: bool = False,
    turned: bool = False,
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
    """
    draw = drawing(view=view, maps=maps)
    frame, render_ms = await timed_async(call=asyncio.to_thread(draw))
    ports.display.show(frame=frame, full=full, turned=turned)
    logger.info(
        'frame drawn', extra={'render_ms': render_ms, 'full': full}
    )
    ports.observer.frame_drawn(render_ms=render_ms)
