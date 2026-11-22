"""Tests for the box over the FROM list."""

from dataclasses import replace

from paperroute_transit.core.chooser_steps import chooser_view, from_inputs
from paperroute_transit.core.kerb_menu import MENU_HINT, kerb_menu, with_kerbs
from paperroute_transit.core.strip_model import HINTS, KerbMenu, StripView
from paperroute_transit.core.strip_view import build_strip_view
from tests.core.chooser_walks import BUSY_PAIR, at_kerbs
from tests.core.test_chooser_steps import pinned


def test_kerb_menu_names_the_stop_and_its_choices() -> None:
    """The box is titled by the station and lists its choices."""
    assert kerb_menu(chooser=at_kerbs(menu=1)) == KerbMenu(
        title='Orchard Close',
        labels=(
            'Both directions', 'Towards Emmanuel Street', 'Towards Oakington',
        ),
        cursor=1,
    )
    busy = kerb_menu(chooser=at_kerbs(pairs=(BUSY_PAIR,), menu=0))
    assert busy is not None and busy.labels == (
        'Towards Emmanuel Street', 'Towards Oakington', 'Both directions',
    )
    assert kerb_menu(chooser=at_kerbs()) is None


def test_with_kerbs_marks_paired_rows_and_opens_the_box() -> None:
    """Paired rows are marked and the open box rides on the view."""
    chooser = at_kerbs(menu=0)
    view = chooser_view(
        state=replace(pinned(), chooser=chooser), chooser=chooser,
    )
    assert isinstance(view, StripView)
    assert [row.name for row in view.rows] == [
        'Oakington', 'Histon', 'Orchard Close', 'Mitcham', 'Emmanuel Street',
    ]
    assert [row.paired for row in view.rows] == [
        False, False, True, False, False,
    ]
    assert view.menu == kerb_menu(chooser=chooser)
    assert view.panel.hint == MENU_HINT


def test_with_kerbs_leaves_a_line_without_pairs_as_built() -> None:
    """With no pairs the view is the plain strip."""
    chooser = at_kerbs(pairs=())
    state = replace(pinned(), chooser=chooser)
    built = build_strip_view(inputs=from_inputs(state=state, chooser=chooser))
    assert with_kerbs(view=built, chooser=chooser) == built
    assert (built.menu, built.panel.hint) == (None, HINTS['FROM'])
