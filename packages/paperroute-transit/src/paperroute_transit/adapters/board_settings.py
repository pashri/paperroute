"""The board's sections of the device kit's Settings page."""

from collections.abc import Mapping
from typing import Final

from paperroute_device.adapters.settings_page import reported_over
from paperroute_transit.adapters.board_web import board_text
from paperroute_transit.adapters.settings_form import (
    BOARD_RADIOS,
    BOARD_SWITCHES,
    ERRORS,
    board_values,
    changed_from,
    posted_values,
)
from paperroute_transit.core.messages import SettingsChanged
from paperroute_transit.core.saved import Settings

SECTIONS: Final[str] = 'settings_sections.html'


class BoardSettingsPlug:
    """
    The Display, Alerts and Timetable sections and how their fields read.

    Parameters
    ----------
    defaults : Settings
        The city's settings, shown until the board reports its own.
    """

    def __init__(self, *, defaults: Settings) -> None:
        self.defaults = defaults
        self.switches: frozenset[str] = frozenset(BOARD_SWITCHES)
        self.radios: frozenset[str] = frozenset(BOARD_RADIOS)
        self.sheets: tuple[str, ...] = ('board.css',)
        self.errors: Mapping[str, str] = ERRORS

    def sections(self) -> str:
        """
        Write the board's sections.

        Returns
        -------
        str
            The sections' lines, their `{{field}}` marks unfilled.
        """
        return board_text(name=SECTIONS)

    def values(self, *, status: Mapping[str, object]) -> dict[str, str]:
        """
        Read the board's form values from the status fields.

        Parameters
        ----------
        status : Mapping[str, object]
            The status fields; `settings` holds the reported values.

        Returns
        -------
        dict[str, str]
            The board's values, over the city's defaults.
        """
        return reported_over(
            values=board_values(settings=self.defaults.board), status=status,
        )

    def change_from(
        self, *, fields: Mapping[str, str],
    ) -> SettingsChanged | None:
        """
        Turn posted fields into the board's change.

        Parameters
        ----------
        fields : Mapping[str, str]
            The posted fields.

        Returns
        -------
        SettingsChanged | None
            The change; None when no board field was posted.
        """
        return changed_from(fields=fields)

    def posted_values(
        self, *, fields: Mapping[str, str], change: object,
    ) -> dict[str, str]:
        """
        Write the posted board fields the way the board will report them.

        Parameters
        ----------
        fields : Mapping[str, str]
            The posted fields.
        change : object
            The SettingsChanged `change_from` read from them.

        Returns
        -------
        dict[str, str]
            The form values of the posted board fields.
        """
        if not isinstance(change, SettingsChanged):
            return {}
        return posted_values(
            fields=fields, event=change, defaults=self.defaults,
        )
