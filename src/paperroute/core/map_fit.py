"""Where a map sits: the fitted zoom moved by the knob's step."""

from paperroute.core.map_view import DEFAULT_ZOOM
from paperroute.core.tiles import ZOOMS


def stepped(*, zoom: int, step: int) -> int:
    """
    Move a fitted zoom by the knob's step.

    Parameters
    ----------
    zoom : int
        The fitted zoom, an index into `ZOOMS`.
    step : int
        Knob detents from the fit; negative is finer.

    Returns
    -------
    int
        The index moved by the step, clamped to `ZOOMS`.
    """
    return min(max(zoom + step, 0), len(ZOOMS) - 1)


def resting_zoom(*, step: int) -> int:
    """
    Give the zoom of a map with nothing to fit.

    Parameters
    ----------
    step : int
        Knob detents; negative is finer.

    Returns
    -------
    int
        `DEFAULT_ZOOM` moved by the step, clamped to `ZOOMS`.
    """
    return stepped(zoom=DEFAULT_ZOOM, step=step)
