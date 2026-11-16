"""What the board pages' heads and navs hold beyond the board's own."""

import html
import re
from dataclasses import dataclass
from typing import Final

from paperroute_device.adapters.web_frame import (
    DEVICE_NAV,
    KIT_NAV,
    LIVE_NAV,
    NAV_MARK,
    NavItem,
    fill_sheets,
    nav_html,
)

SHEET_NAME: Final[re.Pattern[str]] = re.compile(r'^[a-z0-9-]+\.css$')
SCRIPT_NAME: Final[re.Pattern[str]] = re.compile(r'^[a-z0-9-]+\.js$')
TAKEN_SHEETS: Final[frozenset[str]] = frozenset({
    'live.css', 'board.css', 'route-colours.css',
})
SCRIPTS_LINE: Final[str] = '  {{scripts}}\n'
PINS_NAV: Final[NavItem] = NavItem(
    href='/pins',
    label='Pins',
    icon='<path d="M12 22s7-7 7-12a7 7 0 0 0-14 0c0 5 7 12 7 12z"/>'
    '<circle cx="12" cy="10" r="2.5"/>',
)
SETTINGS_NAV: Final[NavItem] = NavItem(
    href='/settings',
    label='Settings',
    icon='<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/>'
    '<circle cx="15" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
)
BOARD_NAV: Final[tuple[NavItem, ...]] = (
    LIVE_NAV,
    PINS_NAV,
    SETTINGS_NAV,
    DEVICE_NAV,
)


def knob_hint(*, vehicle: str) -> str:
    """
    Write the live page's knob hint.

    Parameters
    ----------
    vehicle : str
        What the Strip's knob picks, such as `train` or `bus`.

    Returns
    -------
    str
        The hint's lines, indented to sit in the live page.
    """
    return '\n'.join((
        '        Keys: <kbd>A</kbd> <kbd>S</kbd> <kbd>D</kbd> for the top'
        ' knob,',
        '        <kbd>J</kbd> <kbd>K</kbd> <kbd>L</kbd> for the bottom knob;',
        '        hold <kbd>K</kbd>, or double-click or hold Press, to pin.',
        '        Hold <kbd>S</kbd> and <kbd>K</kbd> together, or both Press',
        '        buttons, for Settings.',
        '        On the Strip, turn while the bottom knob is held to pick a'
        f' {vehicle}:',
        '        hold <kbd>K</kbd> or Press, then press <kbd>J</kbd> or',
        '        <kbd>L</kbd>, or tap Left or Right with another finger.',
    ))


@dataclass(frozen=True)
class BoardHead:
    """
    The extra stylesheets and scripts each board page links, and its nav.

    Attributes
    ----------
    sheets : tuple[str, ...]
        Stylesheet names, served at the site root, linked straight after
        `/live.css` in this order.
    scripts : tuple[str, ...]
        Module script names, served at the site root, loaded where a
        page's scripts line is, in this order.
    nav : tuple[NavItem, ...]
        The nav's links, in order.
    """

    sheets: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    nav: tuple[NavItem, ...] = KIT_NAV

    def __post_init__(self) -> None:
        """
        Check the sheet and script names.

        Raises
        ------
        ValueError
            When a sheet name is not lower-case letters, digits and
            hyphens ending in `.css`, or names a sheet the site already
            serves, which the server could not route twice; or when a
            script name is not lower-case letters, digits and hyphens
            ending in `.js`.
        """
        bad = [
            name
            for name in self.sheets
            if not SHEET_NAME.fullmatch(name) or name in TAKEN_SHEETS
        ]
        if bad:
            raise ValueError(f'unusable sheet names: {bad!r}')
        bad = [
            name for name in self.scripts if not SCRIPT_NAME.fullmatch(name)
        ]
        if bad:
            raise ValueError(f'unusable script names: {bad!r}')

    def fill(self, *, page: str, current: str | None) -> str:
        """
        Fill a raw page's sheets line, scripts line and nav mark.

        Fill before any other value, so text filled in later that reads
        like one of these marks stays as text.

        Parameters
        ----------
        page : str
            The raw page, holding one sheets line, at most one scripts
            line and one nav mark.
        current : str | None
            The href of the open page, marked as current in the nav;
            None marks none.

        Returns
        -------
        str
            The page with a link per sheet and a script element per
            script, leaving no blank line when there are none, and the
            nav drawn in.
        """
        scripts = ''.join(
            f'  <script type="module" src="/{html.escape(name)}"></script>\n'
            for name in self.scripts
        )
        page = fill_sheets(page=page, sheets=self.sheets)
        page = page.replace(SCRIPTS_LINE, scripts, 1)
        return page.replace(
            NAV_MARK, nav_html(items=self.nav, current=current), 1,
        )
