"""Tests for the route chooser screen's view-model."""

from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.factories import place
from paperroute_transit.core.chooser import Choice, Column, Level, opened
from paperroute_transit.core.chooser_lists import (
    pressed,
    previews_failed,
    turned,
)
from paperroute_transit.core.chooser_view import (
    RESERVED_COLUMN,
    build_chooser_view,
    route_title,
)
from paperroute_transit.core.model import Pin
from tests.core.chooser_walks import (
    BUS,
    BUS_606,
    KINDS,
    Catalogue,
    answered,
    at_routes,
    at_to,
    at_towards,
    fed,
    opened_at_kind,
)


def test_view_shows_last_three_columns_with_preview() -> None:
    many = Column(level=Level.ROUTE, items=tuple(
        Choice(key=f'r{number}', label='Somewhere', badge=str(number))
        for number in range(600, 630)
    ))
    catalogue = Catalogue(columns={BUS_606[:2]: many})
    chooser = opened_at_kind(catalogue=catalogue)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, _ = answered(chooser, request, catalogue)
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, _ = answered(chooser, request, catalogue)
    chooser, _ = turned(chooser=chooser, steps=20, reach=None)
    view = build_chooser_view(chooser=chooser)
    assert [column.level for column in view.columns] == [
        Level.GROUP, Level.ROUTE, Level.TOWARDS,
    ]
    assert [column.focused for column in view.columns] == [
        False, True, False,
    ]
    routes = view.columns[1]
    assert (routes.first, routes.total, len(routes.items)) == (12, 30, 11)
    assert routes.items[routes.selected].badge == '620'
    assert routes.selected == 8
    assert view.columns[0].items[view.columns[0].selected].label == '600s'


def test_trail_and_titles() -> None:
    lists = at_towards()
    view = build_chooser_view(chooser=lists)
    assert (view.title, view.trail) == ('Choose a route', 'Bus · 600s · 606')
    assert view.empty is None
    choosing_from, _ = pressed(chooser=lists, reach=None)
    view = build_chooser_view(chooser=choosing_from)
    assert view.title == 'Choose a stop'
    assert view.trail == 'Bus · 600s · 606 · Parramatta'
    assert build_chooser_view(chooser=at_to()).title == 'Choose a destination'


def test_empty_text_while_loading_and_failed() -> None:
    chooser, request = opened(shown=Pin(origin=place('X')), modes=SYDNEY.modes)
    assert build_chooser_view(chooser=chooser).empty == 'Loading…'
    chooser, _ = fed(chooser, request, None)
    view = build_chooser_view(chooser=chooser)
    assert view.empty == 'Routes need a timetable update'
    assert chooser.waiting is None


def test_failed_preview_blanks_only_that_column() -> None:
    chooser, asked = opened(shown=Pin(origin=place('X')), modes=SYDNEY.modes)
    chooser, _ = fed(chooser, asked, KINDS)
    chooser = previews_failed(chooser=chooser, path=())
    view = build_chooser_view(chooser=chooser)
    assert view.empty is None
    assert [column.level for column in view.columns] == [Level.KIND, None]
    assert view.columns[1].note == "Can't load"
    assert view.columns[0].note is None


def test_empty_preview_shows_a_note() -> None:
    chooser = opened_at_kind(catalogue=Catalogue(columns={
        BUS: Column(level=Level.GROUP, items=()),
    }))
    preview = build_chooser_view(chooser=chooser).columns[1]
    assert (preview.note, preview.total, preview.items) == (
        'Nothing here', 0, (),
    )


def test_route_title_names_the_route_then_the_towards_row() -> None:
    """The route number alone, then 'to' the towards row once pressed."""
    assert route_title(chooser=at_towards()) == '606'
    assert route_title(chooser=at_to()) == '606 to Parramatta'


def test_view_reserves_the_preview_while_it_loads() -> None:
    """A focus with a preview shows its slot before the rows arrive."""
    view = build_chooser_view(chooser=at_routes(loaded=False))
    assert [c.level for c in view.columns] == [
        Level.GROUP, Level.ROUTE, None,
    ]
    assert view.columns[-1] == RESERVED_COLUMN


def test_view_without_a_preview_ends_at_the_focus() -> None:
    """A focus with no preview is the last column."""
    view = build_chooser_view(chooser=at_towards())
    assert [c.level for c in view.columns] == [
        Level.GROUP, Level.ROUTE, Level.TOWARDS,
    ]
    assert [c.focused for c in view.columns] == [False, False, True]


def test_view_puts_a_failed_preview_in_its_place() -> None:
    """A failed read shows its note in the slot."""
    chooser = previews_failed(
        chooser=at_routes(loaded=False), path=BUS_606[:2],
    )
    view = build_chooser_view(chooser=chooser)
    assert [c.level for c in view.columns] == [
        Level.GROUP, Level.ROUTE, None,
    ]
    assert view.columns[-1].note == "Can't load"
