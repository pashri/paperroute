"""The runtime wiring a city gives the board."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from paperroute_device.adapters.web_frame import NavItem
    from paperroute_device.assets import AssetRoot
    from paperroute_device.core.device_model import DeviceKey
    from paperroute_transit.adapters.board_pages import WaysPlug
    from paperroute_transit.adapters.web_display import StatusLine
    from paperroute_transit.ports import FeedsClient


class LiveFeeds(Protocol):
    """Makes the client of the city's live feeds."""

    def __call__(self) -> FeedsClient:
        """
        Make the client.

        Returns
        -------
        FeedsClient
            The live client.
        """


class DeviceKeys(Protocol):
    """Gives the API keys the device keeps for the city."""

    def __call__(self) -> Sequence[DeviceKey]:
        """
        Give the keys.

        Returns
        -------
        Sequence[DeviceKey]
            The keys, in declaration order; the probe and the resets
            cover every one.
        """


class SuggestFor(Protocol):
    """Makes the city's suggestion plug for the ways page."""

    def __call__(self, *, live: bool) -> WaysPlug | None:
        """
        Make the plug.

        Parameters
        ----------
        live : bool
            Whether the feeds are live.

        Returns
        -------
        WaysPlug | None
            The plug, or None for no suggestions.
        """


@dataclass(frozen=True)
class CityParts:
    """
    The runtime wiring a city gives the board.

    Attributes
    ----------
    assets : AssetRoot
        The city's web files.
    live_feeds : LiveFeeds
        Makes the live feeds client; never called for replayed feeds.
    keys : DeviceKeys
        Gives the device's API keys; never called for replayed feeds.
    nav : tuple[NavItem, ...]
        The web pages' nav.
    hint : str
        The live page's knob hint.
    suggest : SuggestFor
        Makes the ways page's suggestion plug.
    status_line : StatusLine
        Writes the live page's status line.
    sheets : tuple[str, ...]
        The city's stylesheets in its web directory, linked by every
        page straight after `/live.css`.
    """

    assets: AssetRoot
    live_feeds: LiveFeeds
    keys: DeviceKeys
    nav: tuple[NavItem, ...]
    hint: str
    suggest: SuggestFor
    status_line: StatusLine
    sheets: tuple[str, ...]
