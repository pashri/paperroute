"""The device kit's web display wired with transit's pages, for tests."""

from collections.abc import Callable, Sequence

from paperroute_device.adapters.settings_page import SettingsPage
from paperroute_device.adapters.web_device import DevicePages
from paperroute_device.adapters.web_display import StatusLine, WebDisplay
from paperroute_device.adapters.web_frame import KIT_NAV, NavItem
from paperroute_device.adapters.web_guard import RemoteOf, peer_address
from paperroute_device.adapters.web_live import KNOB_HINT
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.board_head import BoardHead
from paperroute_transit.adapters.board_pages import PinPages
from paperroute_transit.adapters.board_routes import BoardRoutes
from paperroute_transit.adapters.board_settings import BoardSettingsPlug
from paperroute_transit.core.city import City
from paperroute_transit.core.saved import default_settings
from paperroute_transit.transit_app import QUIET_LABEL


# pylint: disable-next=too-many-arguments
def transit_display(
    *,
    city: City = SYDNEY,
    status: Callable[[], dict[str, object]] | None = None,
    device: DevicePages | None = None,
    board: PinPages | None = None,
    nav: Sequence[NavItem] = KIT_NAV,
    hint: str = KNOB_HINT,
    status_line: StatusLine | None = None,
    sheets: Sequence[str] = (),
    remote_of: RemoteOf = peer_address,
) -> WebDisplay:
    """
    Build the web display as the kit's run does for transit.

    Parameters
    ----------
    city : City
        The city, whose modes and defaults the pages use.
    status : Callable[[], dict[str, object]] | None
        Gives the status fields; none by default.
    device : DevicePages | None
        The device pages, or None.
    board : PinPages | None
        The pin pages; ones with no pins and no options by default.
    nav : Sequence[NavItem]
        The nav bar's links.
    hint : str
        The knob hint under the live view.
    status_line : StatusLine | None
        Writes the live view's status line, or None.
    sheets : Sequence[str]
        The app's stylesheets.
    remote_of : RemoteOf
        Reads a request's peer address.

    Returns
    -------
    WebDisplay
        The display, its Settings page on transit's board settings.
    """
    read = status or dict
    plug = BoardSettingsPlug(defaults=default_settings(city=city))
    pins = board or PinPages(
        pins=lambda: None,
        options=None,
        modes=city.modes,
        plug=None,
        head=BoardHead(sheets=tuple(sheets), nav=tuple(nav)),
    )
    display = WebDisplay(
        status=status,
        remote_of=remote_of,
        device=device,
        nav=nav,
        hint=hint,
        status_line=status_line,
        sheets=sheets,
        routes=BoardRoutes(
            modes=city.modes,
            pins=pins,
            reported=lambda: plug.values(status=read()),
        ),
    )
    display.settings = SettingsPage(
        status=read,
        post=display.send,
        plug=plug,
        quiet_label=QUIET_LABEL,
        nav=nav,
        sheets=sheets,
    )
    return display
