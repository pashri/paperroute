"""Draw the arrows of the hooks buses arrive on from roads off the strip."""

from collections.abc import Sequence
from typing import Final

from paperroute_device.render.canvas import Canvas
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.strip_hooks import INWARD, is_road_hook
from paperroute_transit.core.strip_model import (
    HOOK_LENGTH,
    HOOK_TIP,
    INDENT,
    StripRow,
)
from paperroute_transit.render.strip import Columns, hook_arrow

ARROW_SIZE: Final[float] = 1.0  # an exit arrow's scale


def draw_road_hooks(
    canvas: Canvas, *, rows: Sequence[StripRow], columns: Columns,
) -> None:
    """
    Draw each road hook's arrow at its straight's end, pointing in.

    The hook's track is drawn with the other rows' (see `hook_points`).

    Parameters
    ----------
    canvas : Canvas
        Target.
    rows : Sequence[StripRow]
        Visible rows.
    columns : Columns
        Where the strip's parts sit.
    """
    for row in rows:
        if row.kind is RowKind.EXIT and is_road_hook(station=row.station):
            end = columns.track_x + INDENT * row.parent_depth + HOOK_LENGTH
            hook_arrow(
                canvas, x=end + HOOK_TIP, y=row.y, size=ARROW_SIZE,
                bearing=INWARD,
            )
