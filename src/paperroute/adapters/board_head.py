"""What the board pages' heads link beyond the kit's and board's sheets."""

import re
from dataclasses import dataclass
from typing import Final

from paperroute_device.adapters.web_frame import fill_sheets

SHEET_NAME: Final[re.Pattern[str]] = re.compile(r'^[a-z0-9-]+\.css$')
TAKEN_SHEETS: Final[frozenset[str]] = frozenset({
    'live.css', 'board.css', 'route-colours.css',
})


@dataclass(frozen=True)
class BoardHead:
    """
    The extra stylesheets each board page links.

    Attributes
    ----------
    sheets : tuple[str, ...]
        Stylesheet names, served at the site root, linked straight after
        `/live.css` in this order.
    """

    sheets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """
        Check the sheet names.

        Raises
        ------
        ValueError
            When a name is not lower-case letters, digits and hyphens
            ending in `.css`, or names a sheet the site already serves,
            which the server could not route twice.
        """
        bad = [
            name
            for name in self.sheets
            if not SHEET_NAME.fullmatch(name) or name in TAKEN_SHEETS
        ]
        if bad:
            raise ValueError(f'unusable sheet names: {bad!r}')

    def fill(self, *, page: str) -> str:
        """
        Link the sheets in place of a page's sheets line.

        Parameters
        ----------
        page : str
            The raw page, holding one sheets line.

        Returns
        -------
        str
            The page with a link per sheet; no blank line when there are
            none.
        """
        return fill_sheets(page=page, sheets=self.sheets)
