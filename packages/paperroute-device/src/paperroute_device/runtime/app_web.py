"""What an app adds to the board's web pages."""

from collections.abc import Callable
from dataclasses import dataclass

from paperroute_device.adapters.settings_page import SettingsPlug
from paperroute_device.adapters.web_display import AppRoutes, StatusLine
from paperroute_device.adapters.web_frame import KIT_NAV, NavItem
from paperroute_device.adapters.web_live import KNOB_HINT


@dataclass(frozen=True)
class AppWeb:
    """
    The app's part of the web pages.

    Attributes
    ----------
    nav : tuple[NavItem, ...]
        Every page's nav links, in order.
    hint : str
        The live page's knob hint lines, indented; trusted markup.
    sheets : tuple[str, ...]
        The app's stylesheets in its web directory, linked by every page
        straight after `/live.css`.
    status_line : StatusLine | None
        Writes the live page's status line, or None for none.
    routes : AppRoutes | None
        The app's own pages, or None.
    settings : SettingsPlug | None
        The app's Settings sections, or None for the device's alone.
    data : Callable[[], str] | None
        Writes the Device page's Data section, or None for none.
    """

    nav: tuple[NavItem, ...] = KIT_NAV
    hint: str = KNOB_HINT
    sheets: tuple[str, ...] = ()
    status_line: StatusLine | None = None
    routes: AppRoutes | None = None
    settings: SettingsPlug | None = None
    data: Callable[[], str] | None = None
