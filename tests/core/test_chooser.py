"""Tests for the route chooser's state."""

from dataclasses import replace

import pytest

from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_testkit.core.factories import place
from paperroute_transit.adapters.pin_editor import pin_of
from paperroute_transit.core.chooser import (
    Choice,
    Column,
    ColumnRequest,
    Failure,
    Level,
    LineRequest,
    PreviewsRequest,
    Stage,
    StripRequest,
    chosen_pin,
    kind_specs,
    opened,
    path_to,
    pin_value,
    turned_to,
)
from paperroute_transit.core.chooser_lists import (
    backed,
    pressed,
    preview_steps,
    previews_again,
    previews_failed,
    turned,
    with_column,
)
from paperroute_transit.core.chooser_sides import with_line
from paperroute_transit.core.chooser_station import knob_pressed
from paperroute_transit.core.chooser_view import build_chooser_view
from paperroute_transit.core.model import Pin
from paperroute_transit.core.pin_choices import PinDraft
from paperroute_transit.core.reach import Reach, ReachRow, RowKind, StripKey
from tests.core.chooser_walks import (
    BUS,
    GROUPS,
    KINDS,
    LINE,
    METRO_LINES,
    REACH,
    T1,
    T1_PLACES,
    T1_STATIONS,
    T1_TO,
    TOWARDS,
    TRAIN,
    TRAIN_LINES,
    TRAIN_STATIONS,
    TRAIN_TO,
    Catalogue,
    answered,
    at_from,
    at_replacement_towards,
    at_stations,
    at_to,
    at_to_column,
    at_towards,
    fed,
    opened_at_kind,
    row,
)


def test_opened_asks_for_kind_column() -> None:
    """Opened asks for kind column."""
    chooser, request = opened(
        shown=Pin(origin=place('X'), kind=SydneyMode.TRAIN),
        modes=SYDNEY.modes,
    )
    assert request == ColumnRequest(path=())
    assert chooser.waiting == ()
    assert chooser.prefer == SydneyMode.TRAIN
    assert chooser.stage is Stage.LISTS
    assert not chooser.columns


def test_kind_cursor_starts_on_current_kind() -> None:
    """Kind cursor starts on current kind."""
    chooser, asked = opened(
        shown=Pin(origin=place('X'), kind=SydneyMode.TRAIN),
        modes=SYDNEY.modes,
    )
    chooser, request = fed(chooser, asked, KINDS)
    assert chooser.cursors == (1,)
    assert chooser.waiting is None
    assert request == PreviewsRequest(
        path=(), steps=preview_steps(column=KINDS),
    )


def test_kind_column_asks_for_every_kinds_preview() -> None:
    """The KIND column asks for a previews read of every kind."""
    chooser, asked = opened(
        shown=Pin(origin=place('X'), kind=SydneyMode.TRAIN),
        modes=SYDNEY.modes,
    )
    chooser, request = fed(chooser, asked, KINDS)
    assert request == PreviewsRequest(path=(), steps=(
        (Level.KIND, 'bus'), (Level.KIND, 'train'), (Level.KIND, 'metro'),
    ))
    assert (chooser.columns, chooser.waiting) == (
        (replace(KINDS, previews=()),), None,
    )


def test_turned_shows_the_preloaded_preview_and_asks_nothing() -> None:
    """Turning shows a preloaded preview and asks for nothing."""
    chooser, request = turned(
        chooser=opened_at_kind(), steps=1, reach=None,
    )
    assert request is None
    assert (chooser.cursors, chooser.columns[1:]) == ((1, 0), (TRAIN_LINES,))
    chooser, request = turned(chooser=chooser, steps=5, reach=None)
    assert request is None
    assert chooser.columns[1:] == (
        replace(METRO_LINES, collapsed=True), TOWARDS,
    )
    assert turned(chooser=chooser, steps=1, reach=None) == (chooser, None)


def test_turns_while_previews_load_ask_nothing_more() -> None:
    """Turns while the previews are loading ask for nothing more."""
    chooser, asked = opened(shown=Pin(origin=place('X')), modes=SYDNEY.modes)
    chooser, _ = fed(chooser, asked, KINDS)
    for steps in (1, 1, -2):
        chooser, request = turned(chooser=chooser, steps=steps, reach=None)
        assert request is None
        assert len(chooser.columns) == 1


def test_a_turn_after_failed_previews_asks_again() -> None:
    """A turn after a failed previews read asks for them again."""
    chooser, asked = opened(shown=Pin(origin=place('X')), modes=SYDNEY.modes)
    chooser, first = fed(chooser, asked, KINDS)
    chooser = previews_failed(chooser=chooser, path=())
    assert chooser.failed is Failure.LOAD
    chooser, request = turned(chooser=chooser, steps=1, reach=None)
    assert request == first
    assert (chooser.failed, chooser.columns[0].previews) == (None, ())


def test_a_turn_against_the_end_after_failed_previews_asks_again() -> None:
    """A turn that cannot move the cursor still retries a failed read."""
    chooser, asked = opened(shown=Pin(origin=place('X')), modes=SYDNEY.modes)
    chooser, first = fed(chooser, asked, KINDS)
    chooser = previews_failed(chooser=chooser, path=())
    chooser, request = turned(chooser=chooser, steps=-1, reach=None)
    assert request == first
    assert chooser.failed is None


def test_a_turn_against_the_end_with_previews_in_asks_nothing() -> None:
    """Turning against the end of a list with previews loaded asks nothing."""
    chooser, _ = turned(chooser=opened_at_kind(), steps=-20, reach=None)
    again, request = turned(chooser=chooser, steps=-1, reach=None)
    assert (again, request) == (chooser, None)


def test_a_turn_against_the_end_while_previews_load_asks_nothing() -> None:
    """Turning against the end while previews load asks nothing."""
    chooser, asked = opened(shown=Pin(origin=place('X')), modes=SYDNEY.modes)
    chooser, _ = fed(chooser, asked, KINDS)
    again, request = turned(chooser=chooser, steps=-1, reach=None)
    assert (again, request) == (chooser, None)


def test_a_failed_read_after_a_new_timetable_drops_the_old_preview() -> None:
    """A failed read after a new timetable drops the old preview."""
    chooser, request = previews_again(chooser=opened_at_kind())
    assert isinstance(request, PreviewsRequest)
    assert chooser.columns[1:] == (GROUPS,)
    chooser = previews_failed(chooser=chooser, path=request.path)
    assert (chooser.columns, chooser.failed) == ((KINDS,), Failure.LOAD)


def test_press_asks_for_the_new_focus_and_back_keeps_previews() -> None:
    """A press asks for the new focus and back keeps its previews."""
    chooser, request = pressed(chooser=opened_at_kind(), reach=None)
    assert request == PreviewsRequest(
        path=BUS, steps=preview_steps(column=GROUPS),
    )
    chooser, _ = answered(chooser, request)
    back = backed(chooser=chooser)
    assert back is not None
    chooser, request = turned(chooser=back, steps=1, reach=None)
    assert (request, chooser.columns[1:]) == (None, (TRAIN_LINES,))

def test_previews_landing_off_the_focus_are_stored_quietly() -> None:
    """A previews reply for a column that lost the focus is kept there."""
    chooser, request = pressed(chooser=opened_at_kind(), reach=None)
    assert isinstance(request, PreviewsRequest)
    back = backed(chooser=chooser)
    assert back is not None
    before = back.columns[0]
    after, none = answered(back, request)
    assert none is None
    assert after.focus == back.focus
    assert after.columns[0] == before
    assert after.columns[1].previews
    chooser, request = pressed(chooser=after, reach=None)
    assert request is None


def test_stale_column_is_ignored() -> None:
    """Stale column is ignored."""
    chooser = opened_at_kind()
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    after, request = with_column(
        chooser=chooser, path=((Level.KIND, 'bus'),), column=GROUPS,
    )
    assert (after, request) == (chooser, None)


def test_press_before_preview_loaded_does_nothing() -> None:
    """Press before preview loaded does nothing."""
    chooser, asked = opened(shown=Pin(origin=place('X')), modes=SYDNEY.modes)
    chooser, _ = fed(chooser, asked, KINDS)
    after, request = pressed(chooser=chooser, reach=None)
    assert (after, request) == (chooser, None)
    empty, _ = opened(shown=Pin(origin=place('X')), modes=SYDNEY.modes)
    assert pressed(chooser=empty, reach=None) == (empty, None)
    assert turned(chooser=empty, steps=1, reach=None) == (empty, None)
    loading, _ = pressed(chooser=at_towards(), reach=None)
    assert pressed(chooser=loading, reach=None) == (loading, None)
    with pytest.raises(ValueError, match='no From'):
        chosen_pin(chooser=loading, destination=None)

def test_single_line_column_collapses() -> None:
    """Single line column collapses."""
    chooser, request = turned(
        chooser=opened_at_kind(), steps=2, reach=None,
    )
    assert request is None
    assert chooser.columns[1:] == (
        replace(METRO_LINES, collapsed=True), TOWARDS,
    )
    view = build_chooser_view(chooser=chooser)
    assert [column.level for column in view.columns] == [
        Level.KIND, Level.TOWARDS,
    ]
    chooser, asked = pressed(chooser=chooser, reach=None)
    assert chooser.focus == 2
    assert asked is None
    assert build_chooser_view(chooser=chooser).trail == 'Metro · M1'
    chooser, _ = pressed(chooser=chooser, reach=None)
    view = build_chooser_view(chooser=chooser)
    assert view.trail == 'Metro · M1 · Parramatta'

def test_towards_press_asks_for_line() -> None:
    """Towards press asks for line."""
    chooser, request = pressed(chooser=at_towards(), reach=None)
    assert request == LineRequest(towards=11)
    assert chooser.stage is Stage.FROM
    assert chooser.towards == TOWARDS.items[0]
    assert with_line(chooser=chooser, towards=12, line=LINE) == chooser
    loop = (*LINE, LINE[0])
    chooser = with_line(chooser=chooser, towards=11, line=loop)
    assert chooser.line == LINE
    assert with_line(chooser=chooser, towards=11, line=None).failed


def test_from_press_asks_for_strip_with_chosen_routes() -> None:
    """From press asks for strip with chosen routes."""
    chooser, _ = turned(chooser=at_from(), steps=9, reach=None)
    assert chooser.line_cursor == 2
    chooser, _ = turned(chooser=chooser, steps=-1, reach=None)
    chooser, request = pressed(chooser=chooser, reach=None)
    key = StripKey(station='S2', kind=SydneyMode.BUS, routes=('606',))
    assert request == StripRequest(key=key)
    assert chooser.stage is Stage.TO
    assert chooser.origin == LINE[1]
    assert chooser.key == key
    assert chooser.to_cursor == 0


def test_to_press_on_from_commits_no_destination() -> None:
    """To press on from commits no destination."""
    chooser = at_to()
    assert pressed(chooser=chooser, reach=None) == (chooser, None)
    _, pin = pressed(chooser=chooser, reach=REACH)
    assert pin == Pin(
        origin=LINE[1], destination=None, kind=SydneyMode.BUS,
        routes=('606',),
    )


def test_to_press_commits_destination() -> None:
    """To press commits destination."""
    chooser, request = turned(chooser=at_to(), steps=2, reach=REACH)
    assert request is None
    _, pin = pressed(chooser=chooser, reach=REACH)
    assert isinstance(pin, Pin)
    assert pin.destination == place('S4')


def test_to_cursor_skips_approach_rows() -> None:
    """To cursor skips approach rows."""
    chooser, _ = turned(chooser=at_to(), steps=-1, reach=REACH)
    assert chooser.to_cursor == 0
    chooser, _ = turned(chooser=chooser, steps=9, reach=REACH)
    assert chooser.to_cursor == 2
    chooser, _ = turned(chooser=chooser, steps=-9, reach=REACH)
    assert chooser.to_cursor == 0
    assert turned(chooser=chooser, steps=1, reach=None) == (chooser, None)


def test_exit_row_is_never_picked() -> None:
    """Turning the TO cursor across an exit row skips it."""
    reach = replace(REACH, rows=(
        *REACH.rows[:3], row('X1', RowKind.EXIT), *REACH.rows[3:],
    ))
    chooser, _ = turned(chooser=at_to(), steps=1, reach=reach)
    assert chooser.to_cursor == 1
    chooser, _ = turned(chooser=chooser, steps=1, reach=reach)
    assert chooser.to_cursor == 3


def test_back_walks_stages_then_closes() -> None:
    """Back walks stages then closes."""
    chooser = backed(chooser=at_to())
    assert chooser is not None
    assert chooser.stage is Stage.FROM
    assert (chooser.origin, chooser.key) == (None, None)
    assert chooser.line == LINE
    chooser = backed(chooser=chooser)
    assert chooser is not None
    assert chooser.stage is Stage.LISTS
    assert chooser.line == ()
    assert chooser.focus == 3
    steps = []
    while chooser is not None:
        steps.append((chooser.focus, len(chooser.columns)))
        chooser = backed(chooser=chooser)
    assert steps == [(3, 4), (2, 4), (1, 3), (0, 2)]


def test_station_first_walk_commits_line_kind() -> None:
    """Station first walk commits line kind."""
    chooser = at_stations()
    assert chooser.columns[chooser.focus] == T1_STATIONS
    chooser, preview = turned(chooser=chooser, steps=3, reach=None)
    assert preview is None
    chooser, request = knob_pressed(chooser=chooser, reach=None)
    assert request == ColumnRequest(path=(*T1, (Level.STATION, 'E2')))
    chooser, _ = fed(chooser, request, T1_TO)
    _, pin = knob_pressed(chooser=chooser, reach=None)
    assert pin == Pin(
        origin=T1_PLACES['E2'], kind=SydneyMode.TRAIN, routes=('T1',),
    )


def test_the_train_walk_skips_the_line_step() -> None:
    """A train walk has no line step and commits a pin with no routes."""
    chooser = opened_at_kind(
        kind=SydneyMode.TRAIN,
        catalogue=Catalogue(columns={TRAIN: TRAIN_STATIONS}),
    )
    chooser, request = pressed(chooser=chooser, reach=None)
    assert request is None
    chooser, request = turned(chooser=chooser, steps=1, reach=None)
    chooser, request = knob_pressed(chooser=chooser, reach=None)
    assert request == ColumnRequest(path=(*TRAIN, (Level.STATION, 'E2')))
    chooser, _ = fed(chooser, request, TRAIN_TO)
    assert [column.level for column in chooser.columns] == [
        Level.KIND, Level.STATION, Level.TO,
    ]
    _, pin = knob_pressed(chooser=chooser, reach=None)
    assert pin == Pin(origin=T1_PLACES['E2'], kind=SydneyMode.TRAIN)


def test_a_knob_train_pin_has_no_routes() -> None:
    """The knobs make the same pin as the phone editor does."""
    chooser = opened_at_kind(
        kind=SydneyMode.TRAIN,
        catalogue=Catalogue(columns={TRAIN: TRAIN_STATIONS}),
    )
    chooser, _ = pressed(chooser=chooser, reach=None)
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    chooser, request = knob_pressed(chooser=chooser, reach=None)
    chooser, _ = fed(chooser, request, TRAIN_TO)
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    _, pin = knob_pressed(chooser=chooser, reach=None)
    assert isinstance(pin, Pin)
    assert not pin.routes
    assert pin == pin_of(
        draft=PinDraft(kind=SydneyMode.TRAIN),
        origin=T1_PLACES['E2'],
        destination=T1_PLACES['R1'],
        line=None,
        end_point=True,
    )


def test_a_knob_train_pin_without_a_destination_stores_the_from() -> None:
    """Pressing the From's own row stores no Destination and no routes."""
    chooser = opened_at_kind(
        kind=SydneyMode.TRAIN,
        catalogue=Catalogue(columns={TRAIN: TRAIN_STATIONS}),
    )
    chooser, _ = pressed(chooser=chooser, reach=None)
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    chooser, request = knob_pressed(chooser=chooser, reach=None)
    chooser, _ = fed(chooser, request, TRAIN_TO)
    _, pin = knob_pressed(chooser=chooser, reach=None)
    assert isinstance(pin, Pin)
    assert pin.origin == T1_PLACES['E2']
    assert pin.destination is None
    assert not pin.routes


def test_replacement_route_commits_replacement_kind() -> None:
    """Replacement route commits replacement kind."""
    chooser, _ = pressed(chooser=at_replacement_towards(), reach=None)
    chooser = with_line(chooser=chooser, towards=11, line=LINE)
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    chooser, _ = pressed(chooser=chooser, reach=None)
    _, pin = pressed(chooser=chooser, reach=REACH)
    assert pin == Pin(
        origin=LINE[1], kind=SydneyMode.REPLACEMENT, routes=('1T1',),
    )


def test_path_to_uses_choice_level() -> None:
    """Path to uses choice level."""
    chooser = at_stations()
    assert path_to(chooser=chooser, index=chooser.focus)[-1] == (
        Level.STATION, 'R1',
    )
    replacements = Column(level=Level.GROUP, items=(
        Choice(
            key='replacement', label='Trackwork',
            level=Level.REPLACEMENTS,
        ),
    ))
    chooser = opened_at_kind(
        catalogue=Catalogue(columns={BUS: replacements}),
    )
    assert path_to(chooser=chooser, index=1)[-1] == (
        Level.REPLACEMENTS, 'replacement',
    )


def test_empty_column_is_never_focused() -> None:
    """Empty column is never focused."""
    chooser, _ = opened(shown=Pin(origin=place('X')), modes=SYDNEY.modes)
    empty = Column(level=Level.KIND, items=())
    chooser, request = with_column(chooser=chooser, path=(), column=empty)
    assert request is None
    assert chooser.columns == (empty,)
    assert pressed(chooser=chooser, reach=None) == (chooser, None)
    assert turned(chooser=chooser, steps=1, reach=None) == (chooser, None)
    chooser = opened_at_kind(catalogue=Catalogue(columns={
        BUS: Column(level=Level.GROUP, items=()),
    }))
    assert pressed(chooser=chooser, reach=None) == (chooser, None)


def test_replacement_place_starts_on_bus() -> None:
    """Replacement place starts on bus."""
    chooser, request = opened(
        shown=Pin(origin=place('X'), kind=SydneyMode.REPLACEMENT),
        modes=SYDNEY.modes,
    )
    trains_first = replace(KINDS, items=KINDS.items[::-1])
    chooser, _ = fed(chooser, request, trains_first)
    assert chooser.cursors == (2,)


def test_to_press_off_the_strip_commits_no_destination() -> None:
    """To press off the strip commits no destination."""
    chooser, _ = turned(chooser=at_to(), steps=2, reach=REACH)
    shrunk = replace(REACH, rows=REACH.rows[:3])
    _, pin = pressed(chooser=chooser, reach=shrunk)
    assert isinstance(pin, Pin)
    assert pin.destination is None
    _, pin = pressed(chooser=replace(chooser, to_cursor=-1), reach=REACH)
    assert isinstance(pin, Pin)
    assert pin.destination is None


def test_kind_column_from_specs() -> None:
    """The KIND column lists a city's top modes in its declared order."""
    specs = kind_specs(modes=SYDNEY.modes)
    assert [spec.label for spec in specs] == [
        'Bus', 'Train', 'Metro', 'Light rail', 'Ferry', 'School', 'Coach',
    ]
    assert ''.join(spec.letter for spec in specs) == 'BTMLFSC'
    reordered = kind_specs(modes=tuple(reversed(SYDNEY.modes)))
    assert [spec.label for spec in reordered] == [
        'Coach', 'School', 'Ferry', 'Light rail', 'Metro', 'Train', 'Bus',
    ]


def test_replacement_place_starts_on_its_parent_row() -> None:
    """A replacement bus place opens the chooser on Bus."""
    shown = Pin(origin=place('X'), kind=SydneyMode.REPLACEMENT)
    chooser, _ = opened(shown=shown, modes=SYDNEY.modes)
    assert chooser.prefer == SydneyMode.BUS


def test_a_named_line_commits_its_value() -> None:
    """A line whose value differs from its badge pins the value."""
    named = Column(
        level=Level.LINE,
        items=(
            replace(TRAIN_LINES.items[0], value='T1:North Shore & Western'),
            TRAIN_LINES.items[1],
        ),
    )
    chooser = at_to_column(
        catalogue=Catalogue(columns={TRAIN: named}),
    )
    _, pin = knob_pressed(chooser=chooser, reach=None)
    assert pin == Pin(
        origin=T1_PLACES['E2'],
        kind=SydneyMode.TRAIN,
        routes=('T1:North Shore & Western',),
    )


def test_pin_value_falls_back_to_the_badge() -> None:
    """A choice without a value pins its badge."""
    assert pin_value(choice=TRAIN_LINES.items[0]) == 'T1'


def test_passed_earlier_and_dashed_rows_are_not_pickable() -> None:
    """The TO cursor steps over the rows a train Strip adds."""
    kinds = [
        RowKind.EARLIER, RowKind.ORIGIN, RowKind.PASSED, RowKind.DASHED,
        RowKind.MAIN,
    ]
    reach = Reach(
        rows=tuple(
            ReachRow(
                place=place(f'R{i}', name=f'R{i}'),
                kind=kind,
                depth=0,
                above=i < 1,
                parent=None if i == 1 else i - 1,
            )
            for i, kind in enumerate(kinds)
        ),
        stations=1,
        station_of={},
    )
    chooser = turned_to(chooser=at_to(), steps=1, reach=reach)
    assert chooser.to_cursor == 3
    assert turned_to(chooser=chooser, steps=-1, reach=reach).to_cursor == 0
