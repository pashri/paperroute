"""The box over the FROM list offering one direction or both."""

from dataclasses import replace
from typing import Final

from paperroute_device.core.text import HINT_DOT
from paperroute_transit.core.chooser import ChooserState
from paperroute_transit.core.chooser_sides import (
    choice_label,
    pair_at,
    side_choices,
)
from paperroute_transit.core.strip_model import KerbMenu, StripView

MENU_HINT: Final[str] = (
    f'Bottom: press to choose{HINT_DOT}Top: press to cancel'
)
PAIRED_HINT: Final[str] = (
    f'Bottom: press for directions{HINT_DOT}Top: press to go back'
)


def kerb_menu(*, chooser: ChooserState) -> KerbMenu | None:
    """
    Build the box over the cursor's station.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage.

    Returns
    -------
    KerbMenu | None
        The box, or None while it is closed.
    """
    cursor = chooser.kerbs.menu
    if cursor is None or not chooser.line:
        return None
    pair = pair_at(chooser=chooser)
    return KerbMenu(
        title=chooser.line[chooser.line_cursor].name,
        labels=tuple(
            choice_label(
                choice=choice, line=chooser.line, cursor=chooser.line_cursor,
            )
            for choice in side_choices(pair=pair)
        ),
        cursor=cursor,
    )


def with_kerbs(*, view: StripView, chooser: ChooserState) -> StripView:
    """
    Mark the paired rows of a FROM strip and add the box.

    Parameters
    ----------
    view : StripView
        The FROM stage's strip.
    chooser : ChooserState
        Chooser in the FROM stage.

    Returns
    -------
    StripView
        The view with paired rows marked, the box when it is open, and
        the hint for the open box or the paired cursor row on the panel.
    """
    paired = {pair.station for pair in chooser.kerbs.pairs}
    menu = kerb_menu(chooser=chooser)
    panel = view.panel
    if menu is not None:
        panel = replace(panel, hint=MENU_HINT)
    elif pair_at(chooser=chooser) is not None:
        panel = replace(panel, hint=PAIRED_HINT)
    return replace(
        view,
        rows=tuple(
            replace(row, paired=row.station in paired) for row in view.rows
        ),
        menu=menu,
        panel=panel,
    )
