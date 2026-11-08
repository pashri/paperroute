"""What the board pages' heads and navs hold beyond the board's own."""

import html
import re
from dataclasses import dataclass
from typing import Final

from paperroute_device.adapters.web_frame import (
    KIT_NAV,
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
