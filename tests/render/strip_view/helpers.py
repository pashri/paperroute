"""Shared helpers for the strip view tests."""

from dataclasses import replace
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_testkit.render.factories import STRIP_PANEL as PANEL
from paperroute_testkit.render.factories import STRIP_VIEW as VIEW
from paperroute_testkit.render.factories import panel_row
from paperroute_transit.core.geo import Point, to_metres
from paperroute_transit.core.map_view import STRIP_MAP_WIDTH, MapBase, MapView
from paperroute_transit.core.model import CarryOn
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.stop_view import Estimate, PinTag, Row
from paperroute_transit.core.strip_model import StripPanel
from paperroute_transit.render.names import Naming
from paperroute_transit.render.strip_view import draw_strip_view

CENTRE: Point = to_metres(
    lon=151.0036,
    lat=-33.8170,
    projection=SYDNEY_PROJECTION,
)


PANEL_TOP = 128


NAMING: Final[Naming] = Naming(rules=NameRules())


PANEL_STEP = 70


def map_view() -> MapView:
    """
    Make the Strip + map's map.

    Returns
    -------
    MapView
        A 480 px wide map.
    """
    return MapView(
        origin='Town Hall', destination='Parramatta', clock='8:05',
        base=MapBase(
            centre=CENTRE,
            mpp=8,
            route=(),
            width=STRIP_MAP_WIDTH,
            projection=SYDNEY_PROJECTION,
        ),
        zoom=2, markers=(), sidebar=(), banner=None, empty=None,
        tag=PinTag(text='pin 2/3', pinned=True))


MIDDLE = slice(556, 786)


SECOND_LINE = slice(PANEL_TOP + 50, PANEL_TOP + 68)


WHOLE = replace(PANEL, destination=None, no_destination=True)


def panel_frame(
    *, panel: StripPanel, row: Row,
) -> NDArray[np.uint8]:
    """
    Draw the Strip with one row in its panel.

    Parameters
    ----------
    panel : StripPanel
        The panel the row goes in.
    row : Row
        The departure.

    Returns
    -------
    NDArray[np.uint8]
        The frame.
    """
    return draw_strip_view(
        view=replace(VIEW, panel=replace(panel, rows=(row,))),
    )


def central(*, onto: str | None, then: CarryOn | None = None) -> Row:
    """
    Make a T1 departure to Central.

    Parameters
    ----------
    onto : str | None
        Route the train becomes.
    then : CarryOn | None
        Where the train runs on to.

    Returns
    -------
    Row
        The departure.
    """
    return replace(
        panel_row(onto=onto, then=then), destination='Central',
    )


LATE: Final[Estimate] = Estimate(due='8:07', late=4, uncertain=False)
