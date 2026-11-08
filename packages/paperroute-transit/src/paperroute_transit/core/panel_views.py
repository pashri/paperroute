"""What the sleep screen shows."""

from dataclasses import dataclass, replace

from paperroute_transit.core.map_view import MapView


@dataclass(frozen=True)
class SleepView:
    """
    The sleep screen: a map under a band.

    Attributes
    ----------
    map : MapView
        The map drawn.
    band : str
        The text in the band.
    loading : bool
        Whether it was drawn before the shown pin's departures loaded.
    """

    map: MapView
    band: str
    loading: bool = False


def sleep_map(*, view: MapView) -> MapView:
    """
    Strip a Map view to what the sleep screen draws.

    Parameters
    ----------
    view : MapView
        The shown pin's Map view.

    Returns
    -------
    MapView
        The view, and each of its panes, with no clock, sidebar, pairs,
        markers or banner, so it stays the same while time passes.
    """
    return replace(
        view,
        clock='',
        sidebar=(),
        pairs=(),
        markers=(),
        banner=None,
        panes=tuple(sleep_map(view=pane) for pane in view.panes),
    )
